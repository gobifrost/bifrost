"""Pure, bounded Solution archive validation, extraction, and preview parsing.

This module deliberately has no database, object-storage, settings, or provider
dependency.  It operates on caller-owned local archive and workspace paths.
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_SOLUTION_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_SOLUTION_ARCHIVE_ENTRIES = 10_000
MAX_SOLUTION_ENTRY_BYTES = 128 * 1024 * 1024
MAX_SOLUTION_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_SOLUTION_COMPRESSION_RATIO = 100
MAX_SOLUTION_BACKUP_ARCHIVE_BYTES = 32 * 1024 * 1024 * 1024
MAX_SOLUTION_BACKUP_ENTRY_BYTES = 32 * 1024 * 1024 * 1024
MAX_SOLUTION_BACKUP_UNCOMPRESSED_BYTES = 64 * 1024 * 1024 * 1024
PAYLOAD_PREFIX = ".bifrost/file-payloads/"
_DUPLICATE_COMPARE_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class SolutionArchiveLimits:
    archive_bytes: int = MAX_SOLUTION_ARCHIVE_BYTES
    archive_entries: int = MAX_SOLUTION_ARCHIVE_ENTRIES
    entry_bytes: int = MAX_SOLUTION_ENTRY_BYTES
    source_uncompressed_bytes: int = MAX_SOLUTION_UNCOMPRESSED_BYTES
    compression_ratio: int = MAX_SOLUTION_COMPRESSION_RATIO
    backup_archive_bytes: int = MAX_SOLUTION_BACKUP_ARCHIVE_BYTES
    backup_entry_bytes: int = MAX_SOLUTION_BACKUP_ENTRY_BYTES
    backup_uncompressed_bytes: int = MAX_SOLUTION_BACKUP_UNCOMPRESSED_BYTES


DEFAULT_LIMITS = SolutionArchiveLimits()


@dataclass
class PreviewResult:
    """What a Solution archive declares, with no persistence side effects."""

    slug: str | None = None
    name: str | None = None
    scope: str | None = None
    version: str | None = None
    logo: str | None = None
    workflows: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    apps: list[dict[str, Any]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    config_schemas: list[dict[str, Any]] = field(default_factory=list)
    file_locations: list[str] = field(default_factory=list)
    file_policies: list[dict[str, Any]] = field(default_factory=list)
    connection_schemas: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    readme: str | None = None
    requires_password: bool = False


def zip_members_match(
    archive: zipfile.ZipFile, first: zipfile.ZipInfo, duplicate: zipfile.ZipInfo
) -> bool:
    """Compare duplicate entries without buffering their full contents."""
    if first.file_size != duplicate.file_size:
        return False
    with archive.open(first) as first_file, archive.open(duplicate) as duplicate_file:
        while True:
            first_chunk = first_file.read(_DUPLICATE_COMPARE_CHUNK_BYTES)
            duplicate_chunk = duplicate_file.read(_DUPLICATE_COMPARE_CHUNK_BYTES)
            if first_chunk != duplicate_chunk:
                return False
            if not first_chunk:
                return True


def validate_zip_members(
    archive: zipfile.ZipFile, *, limits: SolutionArchiveLimits = DEFAULT_LIMITS
) -> list[zipfile.ZipInfo]:
    """Validate budgets and return one canonical entry per archive path."""
    infos = archive.infolist()
    if len(infos) > limits.archive_entries:
        raise ValueError("Solution archive has too many entries")
    backup = any(info.filename == ".bifrost/secrets.enc" for info in infos)
    total = source_total = 0
    for info in infos:
        payload = backup and info.filename.startswith(PAYLOAD_PREFIX)
        entry_limit = limits.backup_entry_bytes if payload else limits.entry_bytes
        if info.file_size > entry_limit:
            raise ValueError(f"Solution archive entry is too large: {info.filename}")
        if info.compress_size and info.file_size / info.compress_size > limits.compression_ratio:
            raise ValueError(f"Solution archive compression ratio is unsafe: {info.filename}")
        total += info.file_size
        if not payload:
            source_total += info.file_size
        if (
            source_total > limits.source_uncompressed_bytes
            or total > limits.backup_uncompressed_bytes
        ):
            raise ValueError("Solution archive expands beyond the allowed size")

    canonical: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        first = canonical.get(info.filename)
        if first is None:
            canonical[info.filename] = info
        elif not zip_members_match(archive, first, info):
            raise ValueError(
                f"Solution archive has a conflicting duplicate member: {info.filename}"
            )
    return list(canonical.values())


def validate_archive_size(
    archive: zipfile.ZipFile,
    size: int,
    *,
    limits: SolutionArchiveLimits = DEFAULT_LIMITS,
) -> None:
    limit = (
        limits.backup_archive_bytes
        if ".bifrost/secrets.enc" in archive.namelist()
        else limits.archive_bytes
    )
    if size > limit:
        raise ValueError("Solution archive exceeds the compressed upload limit")


def extract_workspace(
    archive: zipfile.ZipFile,
    destination: str,
    *,
    limits: SolutionArchiveLimits = DEFAULT_LIMITS,
) -> None:
    """Extract source and metadata after path/budget checks; retain payloads in ZIP."""
    members = validate_zip_members(archive, limits=limits)
    destination_real = os.path.realpath(destination)
    for member in archive.namelist():
        target = os.path.realpath(os.path.join(destination, member))
        if not (target == destination_real or target.startswith(destination_real + os.sep)):
            raise ValueError(f"unsafe path in zip: {member}")
    for info in members:
        if not info.filename.startswith(PAYLOAD_PREFIX):
            archive.extract(info, destination)


async def validate_payload_integrity(archive: zipfile.ZipFile) -> None:
    """Read skipped encrypted payloads in chunks so ZIP checksums are verified."""
    for info in archive.infolist():
        if info.filename.startswith(PAYLOAD_PREFIX):
            with archive.open(info) as source:
                while source.read(8 * 1024 * 1024):
                    await asyncio.sleep(0)


def safe_extract(
    data: bytes,
    destination: str,
    *,
    limits: SolutionArchiveLimits = DEFAULT_LIMITS,
) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        validate_archive_size(archive, len(data), limits=limits)
        extract_workspace(archive, destination, limits=limits)


def safe_extract_path(
    archive_path: Path,
    destination: str,
    *,
    limits: SolutionArchiveLimits = DEFAULT_LIMITS,
) -> None:
    if archive_path.stat().st_size > limits.backup_archive_bytes:
        raise ValueError("Solution archive exceeds the compressed upload limit")
    with zipfile.ZipFile(archive_path) as archive:
        validate_archive_size(archive, archive_path.stat().st_size, limits=limits)
        extract_workspace(archive, destination, limits=limits)


def parse_workspace(workspace: Path) -> PreviewResult:
    """Reuse CLI collectors to parse an extracted, already-safe workspace."""
    from bifrost.commands.solution import (
        _collect_agents,
        _collect_apps,
        _collect_claims,
        _collect_config_schemas,
        _collect_connection_schemas,
        _collect_events,
        _collect_file_locations,
        _collect_file_policies,
        _collect_forms,
        _collect_python_files,
        _collect_tables,
        _collect_workflows,
    )
    from bifrost.solution_definition import read_solution_definition

    definition = read_solution_definition(workspace)
    _collect_python_files(workspace, definition=definition)
    slug = name = version = logo = None
    if definition.descriptor is not None:
        descriptor = definition.descriptor
        slug, name = descriptor.slug, descriptor.name
        version, logo = descriptor.version, descriptor.logo
    root = os.path.realpath(workspace)
    secrets = os.path.realpath(os.path.join(root, ".bifrost", "secrets.enc"))
    return PreviewResult(
        slug=slug,
        name=name,
        scope=None,
        version=version,
        logo=logo,
        workflows=_collect_workflows(workspace, definition=definition),
        tables=_collect_tables(workspace, definition=definition),
        apps=_collect_apps(workspace, definition=definition),
        forms=_collect_forms(workspace, definition=definition),
        agents=_collect_agents(workspace, definition=definition),
        claims=_collect_claims(workspace, definition=definition),
        config_schemas=_collect_config_schemas(workspace, definition=definition),
        file_locations=_collect_file_locations(workspace, definition=definition),
        file_policies=_collect_file_policies(workspace, definition=definition),
        connection_schemas=_collect_connection_schemas(workspace, definition=definition),
        events=_collect_events(workspace, definition=definition),
        readme=read_readme(workspace),
        requires_password=secrets.startswith(root + os.sep) and os.path.exists(secrets),
    )


def read_readme(workspace: Path) -> str | None:
    root = os.path.realpath(workspace)
    target = os.path.realpath(os.path.join(root, "README.md"))
    if not target.startswith(root + os.sep):
        return None
    path = Path(target)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def preview_zip(data: bytes, *, limits: SolutionArchiveLimits = DEFAULT_LIMITS) -> PreviewResult:
    with tempfile.TemporaryDirectory(prefix="bifrost-zip-preview-") as temporary:
        safe_extract(data, temporary, limits=limits)
        return parse_workspace(Path(temporary))


def preview_zip_path(
    archive_path: Path, *, limits: SolutionArchiveLimits = DEFAULT_LIMITS
) -> PreviewResult:
    with tempfile.TemporaryDirectory(prefix="bifrost-zip-preview-") as temporary:
        safe_extract_path(archive_path, temporary, limits=limits)
        return parse_workspace(Path(temporary))
