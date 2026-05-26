"""ApplicationRepository — moved here from routers/applications.py.

Per the org-scoping consolidation (api/src/repositories/README.md),
repositories live in this directory; the router file becomes the
HTTP-handler-only surface. See docs/plans/2026-05-26-org-scoping-consolidation.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from src.models.contracts.applications import (
    ApplicationCreate,
    ApplicationUpdate,
)
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)


class ApplicationRepository(OrgScopedRepository[Application]):
    """
    Repository for application operations.

    Applications use the CASCADE scoping pattern for org users:
    - Org-specific applications + global (NULL org_id) applications

    Role-based access control:
    - Applications with access_level="role_based" require user to have a role assigned
    - Applications with access_level="authenticated" are accessible to any authenticated user
    """

    model = Application
    role_table = AppRole
    role_entity_id_column = "app_id"

    async def list_applications(self) -> list[Application]:
        """
        List applications with cascade scoping and role-based access.

        Uses the base class scoping and role checking automatically.

        Returns:
            List of Application ORM objects
        """
        # Build base query with cascade scoping
        query = select(self.model)
        query = self._apply_cascade_scope(query)
        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        entities = list(result.scalars().all())

        # Filter by role access for non-superusers with role-based entities
        if not self.is_superuser:
            accessible = []
            for entity in entities:
                if await self._can_access_entity(entity):
                    accessible.append(entity)
            return accessible

        return entities

    async def list_all_in_scope(
        self,
        filter_type: OrgFilterType = OrgFilterType.ALL,
    ) -> list[Application]:
        """
        List all applications in scope without role-based filtering.

        Used by platform admins who bypass role checks.
        Supports all filter types:
        - ALL: No org filter, show everything
        - GLOBAL_ONLY: Only applications with org_id IS NULL
        - ORG_ONLY: Only applications in the specific org (no global fallback)
        - ORG_PLUS_GLOBAL: Applications in the org + global applications

        Args:
            filter_type: How to filter by organization scope

        Returns:
            List of Application ORM objects
        """
        query = select(self.model)

        # Apply org filtering based on filter type
        if filter_type == OrgFilterType.ALL:
            # No org filter - show everything
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            # Only global applications (org_id IS NULL)
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            # Only the specific org, NO global fallback
            if self.org_id is not None:
                query = query.where(self.model.organization_id == self.org_id)
            else:
                # Edge case: ORG_ONLY with no org_id - return nothing
                query = query.where(self.model.id.is_(None))
        elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
            # Cascade scope: org + global
            query = self._apply_cascade_scope(query)

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_slug_global(self, slug: str) -> Application | None:
        """Check if any application exists with this slug (globally unique)."""
        query = select(self.model).where(self.model.slug == slug)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_role_ids(self, app_id: UUID) -> list[UUID]:
        """Get list of role IDs assigned to an application."""
        query = select(AppRole.role_id).where(AppRole.app_id == app_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_application(
        self,
        data: ApplicationCreate,
        created_by: str,
    ) -> Application:
        """Create a new application with access control settings."""
        # Check if application already exists in this scope
        existing = await self.get_by_slug_global(data.slug)
        if existing:
            raise ValueError(f"Application with slug '{data.slug}' already exists")

        application = Application(
            name=data.name,
            slug=data.slug,
            description=data.description,
            icon=data.icon,
            organization_id=self.org_id,
            created_by=created_by,
            access_level=data.access_level,
            repo_path=f"apps/{data.slug}",
        )
        self.session.add(application)
        await self.session.flush()

        # Scaffold initial files via FileStorageService
        await self._scaffold_code_files(application.slug)

        # Add role associations if role_based access
        if data.access_level == "role_based" and data.role_ids:
            for role_id in data.role_ids:
                app_role = AppRole(
                    app_id=application.id,
                    role_id=role_id,
                    assigned_by=created_by,
                )
                self.session.add(app_role)
            await self.session.flush()

        await self.session.refresh(application)

        logger.info(f"Created application '{log_safe(data.slug)}' in org {self.org_id} with access_level={log_safe(data.access_level)}")
        return application

    async def update_application(
        self,
        app_id: UUID,
        data: ApplicationUpdate,
        updated_by: str,
        is_platform_admin: bool = False,
    ) -> Application | None:
        """Update application metadata and access control by ID."""
        application = await self.get(id=app_id)
        if not application:
            return None

        if data.name is not None:
            application.name = data.name
        if data.description is not None:
            application.description = data.description
        if data.icon is not None:
            application.icon = data.icon
        if data.access_level is not None:
            application.access_level = data.access_level

        # Handle slug change with uniqueness check
        if data.slug is not None and data.slug != application.slug:
            # Check if new slug already exists in the same scope
            existing = await self.get_by_slug_global(data.slug)
            if existing and existing.id != application.id:
                raise ValueError(f"Application with slug '{data.slug}' already exists")
            application.slug = data.slug

        # Handle scope change (platform admin only)
        if data.scope is not None and is_platform_admin:
            if data.scope == "global":
                application.organization_id = None
            else:
                try:
                    application.organization_id = UUID(data.scope)
                except ValueError:
                    pass  # Invalid UUID, ignore

        # Update role associations if provided
        if data.role_ids is not None:
            # Delete existing role associations
            existing_roles_query = select(AppRole).where(AppRole.app_id == application.id)
            result = await self.session.execute(existing_roles_query)
            for existing_role in result.scalars().all():
                await self.session.delete(existing_role)

            # Add new role associations (deduplicate to avoid unique constraint violation)
            unique_role_ids = set(data.role_ids)
            for role_id in unique_role_ids:
                app_role = AppRole(
                    app_id=application.id,
                    role_id=role_id,
                    assigned_by=updated_by,
                )
                self.session.add(app_role)

        await self.session.flush()
        await self.session.refresh(application)

        logger.info(f"Updated application '{log_safe(app_id)}'")
        return application

    async def replace_application(
        self,
        app_id: UUID,
        new_repo_path: str,
        *,
        force: bool = False,
    ) -> Application | None:
        """Repoint an application's source directory.

        Validates uniqueness, nesting, and that the new prefix has source files
        in file_index. Any of those checks may be bypassed with ``force=True``.
        No file moves — updates DB only.

        Returns the updated Application, or None if the app was not found.
        Raises ValueError on validation failure.
        """
        from src.models.orm.file_index import FileIndex

        app = await self.get(id=app_id)
        if app is None:
            return None

        # Normalize: strip trailing slash, reject empty string.
        normalized = new_repo_path.rstrip("/")
        if not normalized:
            raise ValueError("repo_path cannot be empty")

        # No-op fast path.
        if normalized == app.repo_path:
            return app

        if not force:
            # Uniqueness check (excluding the app itself).
            existing_stmt = select(Application).where(
                Application.repo_path == normalized,
                Application.id != app_id,
            )
            conflict = (await self.session.execute(existing_stmt)).scalar_one_or_none()
            if conflict is not None:
                raise ValueError(
                    f"repo_path '{normalized}' already claimed by app "
                    f"{conflict.slug} ({conflict.id}). Pass force=True to override."
                )

            # Nesting check: no other app's repo_path is a prefix of new (with /),
            # and new (with /) is not a prefix of any other app's repo_path.
            # Simple Python-side approach: fetch all other apps' repo_paths and check.
            # This is fine because app count is small (tens, not millions).
            new_prefix = f"{normalized}/"
            others_stmt = select(Application).where(Application.id != app_id)
            others = (await self.session.execute(others_stmt)).scalars().all()
            for other in others:
                other_prefix = f"{other.repo_path}/"
                # new is nested inside other: new_prefix starts with other_prefix
                if new_prefix.startswith(other_prefix):
                    raise ValueError(
                        f"repo_path '{normalized}' is nested under app "
                        f"{other.slug} ({other.repo_path}). Pass force=True to override."
                    )
                # other is nested inside new: other_prefix starts with new_prefix
                if other_prefix.startswith(new_prefix):
                    raise ValueError(
                        f"repo_path '{normalized}' would contain app "
                        f"{other.slug} ({other.repo_path}) nested inside it. "
                        "Pass force=True to override."
                    )

            # Source-exists check: at least one file_index row starts with new_prefix.
            file_stmt = select(FileIndex).where(
                FileIndex.path.like(f"{new_prefix}%")
            ).limit(1)
            has_source = (await self.session.execute(file_stmt)).scalar_one_or_none()
            if has_source is None:
                raise ValueError(
                    f"no files found under '{normalized}'. "
                    "Push source first, or pass force=True to repoint ahead of a push."
                )

        app.repo_path = normalized
        await self.session.flush()
        await self.session.refresh(app)

        logger.info(f"Repointed application {log_safe(app_id)} to repo_path={log_safe(normalized)!r}")
        return app

    async def delete_application(self, app_id: UUID) -> bool:
        """Delete an application by ID (cascade deletes pages and components)."""
        application = await self.get(id=app_id)
        if not application:
            return False

        await self.session.delete(application)
        await self.session.flush()

        logger.info(f"Deleted application '{log_safe(app_id)}'")
        return True

    async def publish(
        self,
        app_id: UUID,
        published_by: str,
        message: str | None = None,
    ) -> Application | None:
        """
        Publish draft to live.

        Copies preview files to live in S3 via AppStorageService, then
        captures a published_snapshot for backwards compatibility.
        """
        application = await self.get(id=app_id)
        if not application:
            return None

        # Bundle the app's current source into preview before promoting to
        # live. This replaces the legacy per-file compiler: the bundler is
        # the runtime, so `preview/` must contain a fresh bundle (manifest +
        # hashed chunks) that matches the source being published. A failed
        # bundle MUST fail the publish — we will not promote a stale or
        # partial preview into live.
        from src.services.app_bundler import build_with_migrate
        from src.services.app_storage import AppStorageService
        app_storage = AppStorageService()

        # build_with_migrate runs auto-migration first so a publish from a
        # legacy source tree picks up the rewritten imports before bundling.
        bundle_result, _migrated = await build_with_migrate(
            str(app_id),
            application.repo_prefix,
            "preview",
            dependencies=application.dependencies or {},
        )
        if not bundle_result.success:
            first_err = (bundle_result.errors or [None])[0]
            err_text = first_err.text if first_err else "unknown error"
            raise ValueError(f"Bundle build failed during publish: {err_text}")

        # Promote the freshly-built preview bundle to live.
        published_count = await app_storage.publish(str(app_id))

        if published_count == 0:
            raise ValueError("No files found to publish")

        # Build snapshot for backwards compat
        preview_files = await app_storage.list_files(str(app_id), "preview")
        snapshot = {f: "" for f in preview_files}

        application.published_snapshot = snapshot
        application.published_at = datetime.now(timezone.utc)

        await self.session.flush()
        await self.session.refresh(application)

        logger.info(
            f"Published application {log_safe(app_id)} "
            f"({published_count} files) by user {log_safe(published_by)}"
        )
        return application

    async def _scaffold_code_files(self, slug: str) -> None:
        """Create initial scaffold files for a new app via FileStorageService.

        Creates:
        - _layout.tsx: Root layout wrapper
        - pages/index.tsx: Home page

        Skipped entirely if any file already exists under apps/{slug}/ — the
        caller (e.g. `bifrost apps create` against a slug whose source dir
        was authored locally first) is not expected to lose their work to
        the default Welcome scaffold.
        """
        from src.models.orm.file_index import FileIndex
        from src.services.file_storage import FileStorageService

        prefix = f"apps/{slug}/"
        existing = await self.session.execute(
            select(FileIndex.path).where(FileIndex.path.startswith(prefix)).limit(1)
        )
        if existing.first() is not None:
            logger.info(
                f"Skipped scaffold for app {log_safe(slug)}: files already exist at {log_safe(prefix)}"
            )
            return

        file_storage = FileStorageService(self.session)

        layout_source = '''import { Outlet } from "bifrost";

export default function RootLayout() {
  return (
    <div className="min-h-screen bg-background">
      <Outlet />
    </div>
  );
}
'''
        await file_storage.write_file(
            path=f"apps/{slug}/_layout.tsx",
            content=layout_source.encode("utf-8"),
            updated_by="system",
        )

        index_source = '''export default function HomePage() {
  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-4">Welcome</h1>
      <p className="text-muted-foreground">
        Start building your app by editing this page or adding new files.
      </p>
    </div>
  );
}
'''
        await file_storage.write_file(
            path=f"apps/{slug}/pages/index.tsx",
            content=index_source.encode("utf-8"),
            updated_by="system",
        )

        logger.info(f"Scaffolded initial code files for app {slug}")

    async def export_application(
        self,
        application: Application,
        version_id: UUID | None = None,  # noqa: ARG002 - kept for API compat
    ) -> dict:
        """
        Export application data for API response or GitHub sync.

        Returns a dictionary with application metadata and files from file_index.
        """
        from src.models.orm.file_index import FileIndex

        prefix = application.repo_prefix
        fi_result = await self.session.execute(
            select(FileIndex.path, FileIndex.content).where(
                FileIndex.path.startswith(prefix),
            ).order_by(FileIndex.path)
        )

        files_data: list[dict] = []
        for row in fi_result.all():
            rel_path = row.path[len(prefix):]
            files_data.append({"path": rel_path, "source": row.content or ""})

        role_ids = await self.get_role_ids(application.id)

        return {
            "id": str(application.id),
            "name": application.name,
            "slug": application.slug,
            "description": application.description,
            "icon": application.icon,
            "organization_id": str(application.organization_id) if application.organization_id else None,
            "published_at": application.published_at.isoformat() if application.published_at else None,
            "created_at": application.created_at.isoformat() if application.created_at else None,
            "updated_at": application.updated_at.isoformat() if application.updated_at else None,
            "created_by": application.created_by,
            "is_published": application.is_published,
            "has_unpublished_changes": application.has_unpublished_changes,
            "access_level": application.access_level,
            "role_ids": [str(rid) for rid in role_ids],
            "files": files_data,
        }

    async def update_draft_files(
        self,
        application: Application,
        files_data: list[dict],
    ) -> None:
        """
        Replace all files in the app with the provided files via FileStorageService.

        Args:
            application: The application to update
            files_data: List of file dictionaries with 'path' and 'source'
        """
        from src.services.file_storage import FileStorageService

        file_storage = FileStorageService(self.session)
        prefix = application.repo_prefix

        # Delete existing files
        from src.models.orm.file_index import FileIndex
        existing_result = await self.session.execute(
            select(FileIndex.path).where(
                FileIndex.path.startswith(prefix),
            )
        )
        for (path,) in existing_result.all():
            await file_storage.delete_file(path)

        # Write new files
        for file_dict in files_data:
            full_path = f"{prefix}{file_dict['path']}"
            source = file_dict.get("source", "")
            await file_storage.write_file(
                path=full_path,
                content=source.encode("utf-8"),
                updated_by="system",
            )

    async def rollback_to_version(
        self,
        application: Application,
        version_id: UUID,  # noqa: ARG002
    ) -> None:
        """
        Rollback is no longer supported with the unified file storage model.
        Published snapshots are immutable point-in-time captures.
        """
        raise ValueError("Version rollback is not supported. Use published snapshots instead.")

