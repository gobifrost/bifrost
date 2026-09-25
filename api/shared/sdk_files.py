"""Shared business service for cloud-mode SDK file reads.

Single implementation used by the HTTP handlers
(``api/src/routers/files.py``) serving external SDK callers. A future
parent-side local dispatcher will call the same service with a
parent-derived :class:`FileCaller` so HTTP and local results are identical
by construction.

Covers ``files.read``/``read_bytes``, ``files.list`` (without
``include_metadata``), ``files.exists``, and ``files.stat``. Service
extraction only: no local transport, SDK facade, dispatcher, binary
framing, or parent principal work. The workspace ``include_metadata=True``
branch stays in the router (the Python SDK never requests it).

All failures raise :class:`FileServiceError` (transport-neutral); the HTTP
adapter maps them to ``HTTPException``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from shared.file_access import (
    FileCaller,
    FileServiceError,
    authorize_file_policy,
    ctx_solution_id,
    deny_file_policy,
    filter_listed_paths,
    get_file_stat,
    require_declared_solution_file_location,
    tiers_for_backend_mode,
)

logger = logging.getLogger(__name__)


@dataclass
class FileReadResult:
    """Raw read result — the transport encodes text/base64."""

    content: bytes
    binary: bool


async def sdk_read_file(
    caller: FileCaller,
    *,
    path: str,
    location: str,
    scope: str | None,
    mode: str = "cloud",
    binary: bool = False,
) -> FileReadResult:
    """Read a file across the ``file_read_tiers`` cascade.

    Preserves tier order, inbound Solution gates, declared-location gates,
    workspace-only authority, mode filtering, per-tier policy probes, the
    final-deny audit, and 404/403/400 status semantics: a path missing
    across allowed tiers is 404, while a path denied in all tiers raises
    the audited 403.
    """
    from src.services.file_backend import get_backend
    from src.services.solution_scope import file_read_tiers

    try:
        if location != "workspace":
            await require_declared_solution_file_location(
                caller,
                solution_id=ctx_solution_id(caller, location),
                location=location,
            )
        tiers = tiers_for_backend_mode(
            await file_read_tiers(caller.db, caller, location, scope),
            mode,
        )
        backend = get_backend(mode, caller.db)
        content: bytes | None = None
        had_allowed_tier = False
        for tier in tiers:
            if not await authorize_file_policy(
                caller,
                action="exists",
                location=location,
                scope=tier.scope,
                path=path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            ):
                continue
            had_allowed_tier = True
            try:
                content = await backend.read(path, location, scope=tier.scope)
                break
            except FileNotFoundError:
                continue

        if content is None:
            if not had_allowed_tier:
                await deny_file_policy(
                    caller,
                    action="read",
                    location=location,
                    path=path,
                    scope=scope,
                    solution_id=ctx_solution_id(caller, location),
                )
            raise FileServiceError(404, f"File not found: {path}")

        if not binary:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FileServiceError(
                    400, "File is binary. Use binary=true to read as base64."
                ) from exc
        return FileReadResult(content=content, binary=binary)
    except FileServiceError:
        raise
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc


async def sdk_list_files(
    caller: FileCaller,
    *,
    directory: str,
    location: str,
    scope: str | None,
    mode: str = "cloud",
) -> list[str]:
    """List a directory across tiers with per-file policy filtering."""
    from src.services.file_backend import get_backend
    from src.services.solution_scope import file_read_tiers

    try:
        if location != "workspace":
            await require_declared_solution_file_location(
                caller,
                solution_id=ctx_solution_id(caller, location),
                location=location,
            )
        tiers = tiers_for_backend_mode(
            await file_read_tiers(caller.db, caller, location, scope),
            mode,
        )
        if not tiers:
            return []
        primary_tier = tiers[0]
        directory_allowed = await authorize_file_policy(
            caller,
            action="list",
            location=location,
            scope=primary_tier.scope,
            path=directory,
            solution_id=primary_tier.solution_id,
            organization_id=primary_tier.organization_id,
        )

        backend = get_backend(mode, caller.db)
        files: list[str] = []
        seen: set[str] = set()
        any_directory_allowed = directory_allowed
        for index, tier in enumerate(tiers):
            tier_directory_allowed = await authorize_file_policy(
                caller,
                action="list",
                location=location,
                scope=tier.scope,
                path=directory,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            any_directory_allowed = any_directory_allowed or tier_directory_allowed
            # The primary tier (the caller's own scope) is always enumerated
            # and filtered per-file; fallback tiers are gated by their
            # directory-level list policy.
            if index > 0 and not tier_directory_allowed:
                continue
            tier_files = await backend.list(directory, location, scope=tier.scope)
            tier_files = await filter_listed_paths(
                caller,
                paths=sorted(tier_files),
                location=location,
                scope=tier.scope,
                action="list",
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            for path in tier_files:
                if path in seen:
                    continue
                seen.add(path)
                files.append(path)
        if not any_directory_allowed and not files:
            await deny_file_policy(
                caller,
                action="list",
                location=location,
                path=directory,
                scope=scope,
                solution_id=primary_tier.solution_id,
            )
        return files
    except FileServiceError:
        raise
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc


async def sdk_file_exists(
    caller: FileCaller,
    *,
    path: str,
    location: str,
    scope: str | None,
    mode: str = "cloud",
) -> bool:
    """Existence probe across allowed tiers (never 403/404 — just False)."""
    from src.services.file_backend import get_backend
    from src.services.solution_scope import file_read_tiers

    try:
        if location != "workspace":
            await require_declared_solution_file_location(
                caller,
                solution_id=ctx_solution_id(caller, location),
                location=location,
            )
        tiers = tiers_for_backend_mode(
            await file_read_tiers(caller.db, caller, location, scope),
            mode,
        )
        backend = get_backend(mode, caller.db)
        for tier in tiers:
            allowed = await authorize_file_policy(
                caller,
                action="read",
                location=location,
                scope=tier.scope,
                path=path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            if not allowed:
                continue
            exists = await backend.exists(path, location, scope=tier.scope)
            if exists:
                return True
        return False
    except FileServiceError:
        raise
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc


async def sdk_file_stat(
    caller: FileCaller,
    *,
    path: str,
    location: str,
    scope: str | None,
    mode: str = "cloud",
):
    """File metadata across allowed tiers (``exists=False`` when absent)."""
    from src.services.solution_scope import file_read_tiers

    try:
        if location != "workspace":
            await require_declared_solution_file_location(
                caller,
                solution_id=ctx_solution_id(caller, location),
                location=location,
            )
        tiers = tiers_for_backend_mode(
            await file_read_tiers(caller.db, caller, location, scope),
            mode,
        )
        for tier in tiers:
            allowed = await authorize_file_policy(
                caller,
                action="read",
                location=location,
                scope=tier.scope,
                path=path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            if not allowed:
                continue
            stat = await get_file_stat(
                caller.db, path, location, tier.scope, mode
            )
            if stat.exists:
                return stat
        from src.models import FileStatResponse

        return FileStatResponse(path=path, exists=False)
    except FileServiceError:
        raise
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc


__all__ = [
    "FileCaller",
    "FileReadResult",
    "FileServiceError",
    "sdk_file_exists",
    "sdk_file_stat",
    "sdk_list_files",
    "sdk_read_file",
]
