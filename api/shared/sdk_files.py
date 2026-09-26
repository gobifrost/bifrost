"""Shared business service for cloud-mode SDK file operations.

Single implementation used by the HTTP handlers
(``api/src/routers/files.py``) serving external SDK callers and reached by
workflow children over the worker-local engine socket with a
parent-derived :class:`FileCaller` so results are identical by
construction.

Covers ``files.read``/``read_bytes``, ``files.list`` (without
``include_metadata``), ``files.exists``, ``files.stat``, ``files.write``,
``files.delete``, and signed-URL presigning (``PUT`` uploads and ``GET``
downloads). Service extraction only: no SDK facade, binary framing, or
parent principal work. The workspace
``include_metadata=True`` branch stays in the router (the Python SDK never
requests it), as do ``/search`` (admin-only global text-index path) and
browser ``/complete-upload``.

All failures raise :class:`FileServiceError` (transport-neutral); the HTTP
adapter maps them to ``HTTPException``. A raw ``ValueError`` still escapes
only where the router historically let it reach the 422 middleware (an
invalid superuser ``scope`` on the PUT signed-URL path); every other
validation failure is a ``FileServiceError`` with an HTTP-style status.
"""

from __future__ import annotations

import base64
import hashlib
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
    install_org_id,
    lock_file_mutation,
    require_declared_solution_file_location,
    require_file_policy,
    resolve_effective_scope,
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


async def sdk_write_file(
    caller: FileCaller,
    *,
    path: str,
    content: str,
    binary: bool,
    location: str,
    scope: str | None,
    mode: str = "cloud",
    expected_version: str | None = None,
    create_only: bool = False,
) -> None:
    """Write a file with guarded-conflict semantics.

    Preserves the HTTP route's exact order: effective scope, declared
    Solution location and inbound gates, write-policy check with denial
    audit, advisory lock, ``expected_version``/``create_only`` conflict
    checks, content decoding, backend write, and — cloud mode only — cloud
    metadata upsert with commit before file-change publish. Local mode
    performs no metadata, commit, or publish side effects.
    """
    from src.core.log_safety import log_safe
    from src.services.file_backend import get_backend

    try:
        effective_scope = resolve_effective_scope(caller, location, scope)
        solution_id = ctx_solution_id(caller, location)
        await require_declared_solution_file_location(
            caller,
            solution_id=solution_id,
            location=location,
        )
        await require_file_policy(
            caller,
            action="write",
            location=location,
            scope=effective_scope,
            path=path,
            solution_id=solution_id,
        )
        if create_only and expected_version is not None:
            raise FileServiceError(
                400, "create_only and expected_version cannot be combined"
            )
        await lock_file_mutation(
            caller.db,
            location=location,
            scope=effective_scope,
            path=path,
        )

        if create_only or expected_version is not None:
            current_stat = await get_file_stat(
                caller.db,
                path,
                location,
                effective_scope,
                mode,
            )
            if create_only and current_stat.exists:
                raise FileServiceError(
                    409,
                    {
                        "reason": "file_exists",
                        "path": path,
                        "message": "File already exists; read it before replacing it.",
                        "current_version": current_stat.version,
                        "current_last_modified": current_stat.last_modified,
                        "current_updated_by": current_stat.updated_by,
                    },
                )
            if expected_version is not None:
                if not current_stat.exists:
                    raise FileServiceError(
                        409,
                        {
                            "reason": "file_missing",
                            "path": path,
                            "expected_version": expected_version,
                            "message": "File no longer exists.",
                        },
                    )
                if current_stat.version != expected_version:
                    raise FileServiceError(
                        409,
                        {
                            "reason": "version_conflict",
                            "path": path,
                            "expected_version": expected_version,
                            "current_version": current_stat.version,
                            "message": "File changed after it was read.",
                            "current_last_modified": current_stat.last_modified,
                            "current_updated_by": current_stat.updated_by,
                        },
                    )

        if binary:
            content_bytes = base64.b64decode(content)
        else:
            content_bytes = content.encode("utf-8")

        updated_by = caller.user.email if caller.user else "system"
        backend = get_backend(mode, caller.db)
        await backend.write(
            path, content_bytes, location, updated_by, scope=effective_scope
        )
        if mode == "cloud":
            from shared.file_paths import resolve_s3_key
            from src.core.pubsub import publish_file_change
            from src.services.file_storage import FileStorageService
            from src.services.file_storage.s3_client import S3StorageClient

            s3_path = resolve_s3_key(location, effective_scope, path)
            await FileStorageService(caller.db).record_file_write_metadata(
                location=location,
                scope=effective_scope,
                path=path,
                s3_path=s3_path,
                content_type=S3StorageClient.guess_content_type(path),
                size_bytes=len(content_bytes),
                sha256=hashlib.sha256(content_bytes).hexdigest(),
                updated_by=updated_by,
                user_id=str(caller.user.user_id),
                solution_id=solution_id,
                org_id=await install_org_id(caller, solution_id),
            )
            # The yielded DB dependency commits after the response body is
            # sent. Commit here so a successful write response and its
            # notification never race ahead of durable metadata.
            await caller.db.commit()
            await publish_file_change(
                location=location,
                scope=effective_scope,
                path=path,
                action="write",
            )

        logger.info(
            f"Wrote file: {log_safe(path)} ({len(content_bytes)} bytes, "
            f"mode={log_safe(mode)}, location={log_safe(location)})"
        )
    except FileServiceError:
        raise
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc


async def sdk_delete_file(
    caller: FileCaller,
    *,
    path: str,
    location: str,
    scope: str | None,
    mode: str = "cloud",
    expected_version: str | None = None,
) -> None:
    """Delete a file with guarded-conflict semantics.

    Preserves the HTTP route's exact order: effective scope, declared
    Solution location and inbound gates, delete-policy check with denial
    audit, advisory lock, ``expected_version`` conflict checks, backend
    delete, and — cloud mode only — cloud metadata delete with commit
    before file-change publish. Local mode performs no metadata, commit,
    or publish side effects. A backend ``FileNotFoundError`` is 404.
    """
    from src.core.log_safety import log_safe
    from src.services.file_backend import get_backend

    try:
        effective_scope = resolve_effective_scope(caller, location, scope)
        solution_id = ctx_solution_id(caller, location)
        await require_declared_solution_file_location(
            caller,
            solution_id=solution_id,
            location=location,
        )
        await require_file_policy(
            caller,
            action="delete",
            location=location,
            scope=effective_scope,
            path=path,
            solution_id=solution_id,
        )
        backend = get_backend(mode, caller.db)

        await lock_file_mutation(
            caller.db,
            location=location,
            scope=effective_scope,
            path=path,
        )

        if expected_version is not None:
            current_stat = await get_file_stat(
                caller.db,
                path,
                location,
                effective_scope,
                mode,
            )
            if not current_stat.exists:
                raise FileServiceError(
                    409,
                    {
                        "reason": "file_missing",
                        "path": path,
                        "expected_version": expected_version,
                        "message": "File no longer exists.",
                    },
                )
            if current_stat.version != expected_version:
                raise FileServiceError(
                    409,
                    {
                        "reason": "version_conflict",
                        "path": path,
                        "expected_version": expected_version,
                        "current_version": current_stat.version,
                        "message": "File changed after it was read.",
                        "current_last_modified": current_stat.last_modified,
                        "current_updated_by": current_stat.updated_by,
                    },
                )
        await backend.delete(path, location, scope=effective_scope)
        if mode == "cloud":
            from src.core.pubsub import publish_file_change
            from src.services.file_policy_service import FilePolicyService

            await FilePolicyService(caller.db).delete_metadata(
                organization_id=await install_org_id(caller, solution_id),
                location=location,
                path=path,
                solution_id=solution_id,
            )
            # Do not acknowledge or publish the deletion while its metadata
            # is still visible to another transaction.
            await caller.db.commit()
            await publish_file_change(
                location=location,
                scope=effective_scope,
                path=path,
                action="delete",
            )

        logger.info(
            f"Deleted file: {log_safe(path)} "
            f"(mode={log_safe(mode)}, location={log_safe(location)})"
        )
    except FileServiceError:
        raise
    except FileNotFoundError as exc:
        raise FileServiceError(404, f"File not found: {path}") from exc
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc


@dataclass
class SignedUrlResult:
    """Presigned URL — the transport wraps it in its own DTO."""

    url: str
    path: str
    expires_in: int


async def sdk_signed_url(
    caller: FileCaller,
    *,
    path: str,
    location: str,
    scope: str | None,
    method: str = "PUT",
    content_type: str = "application/octet-stream",
    expires_in: int = 600,
) -> SignedUrlResult:
    """Resolve and presign a direct-upload (PUT) or download (GET) URL.

    Signed GET preserves the tier cascade: per-tier ``signed_get`` policy
    probes, first-existing-permitted-object preference, presigning a
    permitted-but-missing object when nothing exists, and one audited
    all-tier deny. Signed PUT preserves scope/location/policy resolution
    and presigns without any metadata mutation.
    """
    from shared.file_paths import resolve_s3_key
    from src.services.file_storage import FileStorageService

    try:
        if method == "GET":
            try:
                if location != "workspace":
                    await require_declared_solution_file_location(
                        caller,
                        solution_id=ctx_solution_id(caller, location),
                        location=location,
                    )
                from src.services.solution_scope import file_read_tiers

                tiers = await file_read_tiers(caller.db, caller, location, scope)
                if len(tiers) == 1:
                    tier = tiers[0]
                    s3_path = resolve_s3_key(location, tier.scope, path)
                    await require_file_policy(
                        caller,
                        action="signed_get",
                        location=location,
                        scope=tier.scope,
                        path=path,
                        solution_id=tier.solution_id,
                        organization_id=tier.organization_id,
                    )
                else:
                    from src.services.file_backend import get_backend

                    backend = get_backend("cloud", caller.db)
                    allowed_path: str | None = None
                    for tier in tiers:
                        s3_path = resolve_s3_key(location, tier.scope, path)
                        if not await authorize_file_policy(
                            caller,
                            action="signed_get",
                            location=location,
                            scope=tier.scope,
                            path=path,
                            solution_id=tier.solution_id,
                            organization_id=tier.organization_id,
                        ):
                            continue
                        allowed_path = allowed_path or s3_path
                        if await backend.exists(
                            path,
                            location,
                            scope=tier.scope,
                        ):
                            allowed_path = s3_path
                            break
                    if allowed_path is None:
                        await deny_file_policy(
                            caller,
                            action="signed_get",
                            location=location,
                            path=path,
                            scope=scope,
                            solution_id=ctx_solution_id(caller, location),
                        )
                    s3_path = allowed_path
                url = await FileStorageService(
                    caller.db
                ).generate_presigned_download_url(
                    path=s3_path,
                    expires_in=expires_in,
                )
            except ValueError as exc:
                raise FileServiceError(400, str(exc)) from exc
        else:
            # PUT: resolve the single effective scope, gate, presign. A
            # ValueError from scope resolution propagates raw (the router
            # historically let it reach the 422 middleware); only resolver
            # rejections become 400 — mirroring the previous handler.
            effective_scope = resolve_effective_scope(caller, location, scope)
            solution_id = ctx_solution_id(caller, location)
            await require_declared_solution_file_location(
                caller,
                solution_id=solution_id,
                location=location,
            )
            try:
                s3_path = resolve_s3_key(location, effective_scope, path)
            except ValueError as exc:
                raise FileServiceError(400, str(exc)) from exc
            await require_file_policy(
                caller,
                action="signed_put",
                location=location,
                scope=effective_scope,
                path=path,
                content_type=content_type,
                solution_id=solution_id,
            )
            url = await FileStorageService(caller.db).generate_presigned_upload_url(
                path=s3_path,
                content_type=content_type,
                expires_in=expires_in,
            )
        return SignedUrlResult(url=url, path=s3_path, expires_in=expires_in)
    except FileServiceError:
        raise


__all__ = [
    "FileCaller",
    "FileReadResult",
    "FileServiceError",
    "SignedUrlResult",
    "sdk_delete_file",
    "sdk_file_exists",
    "sdk_file_stat",
    "sdk_list_files",
    "sdk_read_file",
    "sdk_signed_url",
    "sdk_write_file",
]
