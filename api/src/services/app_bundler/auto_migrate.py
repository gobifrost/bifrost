"""
Auto-migration of app source during lazy-build.

Runs `bifrost.migrate_imports` against S3 `_repo/<repo_prefix>/` before the
bundler builds for the first time. Intended to get un-migrated apps on the
new esbuild runtime without the developer having to `bifrost pull` +
`bifrost migrate-imports` + `bifrost push` manually for every legacy app.

Invoked from `get_bundle_manifest` on `FileNotFoundError` for the bundle
manifest — BEFORE the build step. Not invoked from the save loop: the save
loop runs during active editing, the developer already has a local copy, and
mutating _repo out from under them would desync their workspace.
"""
from __future__ import annotations

import logging
import pathlib
import tempfile

from bifrost.migrate_imports import (
    FileMigrationResult,
    load_lucide_icon_names,
    migrate_app,
)
from bifrost.platform_names import PLATFORM_EXPORT_NAMES
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.services.file_index_service import FileIndexService
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)


async def auto_migrate_repo_prefix(
    app_id: str,
    repo_prefix: str,
    db: AsyncSession,
) -> tuple[bool, list[FileMigrationResult]]:
    """
    Read every TSX/TS file under `_repo/<repo_prefix>`, run the import
    migrator over them, and write back any changes.

    Returns `(migrated, results)`:
      - migrated: True iff at least one file was rewritten and written back
      - results:  every per-file result (changed + unchanged), for logging

    Idempotent — a second call finds nothing to do (migrator is no-op on
    already-migrated code) and returns (False, results).
    """
    repo = RepoStorage()
    if not repo_prefix.endswith("/"):
        repo_prefix += "/"

    # 1. Materialize the app into a tempdir mirroring the _repo layout so
    #    `migrate_app`'s filesystem-oriented helpers (list_user_components,
    #    find_source_files) work unchanged.
    with tempfile.TemporaryDirectory(prefix="bifrost-automigrate-") as tmp:
        tmp_root = pathlib.Path(tmp)
        app_dir = tmp_root / "app"
        app_dir.mkdir()

        keys = await repo.list(repo_prefix)
        rel_paths: list[str] = []
        for key in keys:
            rel = key[len(repo_prefix):]
            if not rel or rel.endswith("/"):
                continue
            if rel == "app.yaml" or ".tmp." in rel:
                continue
            data = await repo.read(key)
            dest = app_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rel_paths.append(rel)

        if not rel_paths:
            return False, []

        # 2. Run the migrator. Load lucide icon names from the client's
        #    installed copy (mounted into the API container at build time).
        lucide_names = load_lucide_icon_names()
        results = migrate_app(app_dir, PLATFORM_EXPORT_NAMES, lucide_names)

        # 3. Write back every file that actually changed, only changed bytes.
        changed = [r for r in results if r.changed]
        if not changed:
            logger.info(
                f"Auto-migrated app={log_safe(app_id)} files=0 changes=none (already migrated)"
            )
            return False, results

        summary_parts: list[str] = []
        # Index in the caller's session: the save path already holds this
        # file's index row lock in it, so a second session would wait forever.
        index = FileIndexService(db, repo)
        for r in changed:
            rel_path = str(r.path.relative_to(app_dir))
            await index.write(repo_prefix + rel_path, r.updated.encode("utf-8"))
        for r in changed:
            rel_path = str(r.path.relative_to(app_dir))
            moves: list[str] = []
            if r.moved_icons:
                moves.append(f"{r.moved_icons}icon")
            if r.moved_router:
                moves.append(f"{r.moved_router}router")
            if r.added_components:
                moves.append(f"{r.added_components}added")
            summary_parts.append(f"{rel_path}({','.join(moves) or 'rewrite'})")

        logger.info(
            f"Auto-migrated app={log_safe(app_id)} files={len(changed)} "
            f"changes={'; '.join(summary_parts)}"
        )
        return True, results
