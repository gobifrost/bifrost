"""Admin-only STRUCTURAL enumeration of file shares/folders/files.

This is NOT policy-gated: it reports what physically exists in a scope so the
explorer tree never orphans a file. Content access (read/write/...) stays
policy-governed elsewhere. Excludes reserved locations (workspace/temp);
includes `uploads` flagged read-only.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_paths import UPLOADS_PREFIX, resolve_s3_key
from src.services.file_policy_service import FilePolicyService
from src.services.file_storage import FileStorageService

# Top-level S3 prefixes that map to reserved/internal locations and must never
# appear as explorer shares.
_HIDDEN_TOP_PREFIXES = {"_repo", "_tmp", "_apps", "_solutions", "_solution_artifacts"}
_UPLOADS_TOP = UPLOADS_PREFIX.rstrip("/")


class StructureEntry(BaseModel):
    name: str
    kind: Literal["folder", "file"]
    path: str  # relative to location root (no scope segment)


class ShareEntry(BaseModel):
    location: str
    read_only: bool
    has_policy: bool


def _scope_seg(org_id: UUID | None) -> str:
    return "global" if org_id is None else str(org_id)


class FileStructureService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = FileStorageService(db)
        self.policies = FilePolicyService(db)

    async def list_prefix(
        self, *, org_id: UUID | None, location: str, prefix: str
    ) -> list[StructureEntry]:
        scope = _scope_seg(org_id)
        base = resolve_s3_key(location, scope, prefix)
        if prefix and not base.endswith("/"):
            base += "/"
        keys = await self.storage.list_raw_s3(base)
        folders: dict[str, StructureEntry] = {}
        files: dict[str, StructureEntry] = {}
        rel_prefix = prefix.rstrip("/") + "/" if prefix else ""
        for key in keys:
            rel = key[len(base):]
            if not rel:
                continue
            head, _, tail = rel.partition("/")
            if tail:  # nested → folder
                folders[head] = StructureEntry(
                    name=head, kind="folder", path=f"{rel_prefix}{head}"
                )
            else:
                files[head] = StructureEntry(
                    name=head, kind="file", path=f"{rel_prefix}{head}"
                )
        return sorted(
            [*folders.values(), *files.values()],
            key=lambda e: (e.kind != "folder", e.name),
        )

    async def list_shares(self, *, org_id: UUID | None) -> list[ShareEntry]:
        scope = _scope_seg(org_id)
        # Locations carrying files in this scope: bucket every key's top segment.
        all_keys = await self.storage.list_raw_s3("")
        file_locations: set[str] = set()
        for key in all_keys:
            top, _, rest = key.partition("/")
            if not rest:
                continue
            if top in _HIDDEN_TOP_PREFIXES:
                continue
            # Both uploads and custom locations are laid out {location}/{scope}/...
            seg2 = rest.split("/", 1)[0]
            if seg2 != scope:
                continue
            file_locations.add("uploads" if top == _UPLOADS_TOP else top)
        # Locations carrying a policy in this scope (so a freshly-policied,
        # empty share still appears until a file lands).
        policy_rows = await self.policies.list_policies(organization_id=org_id)
        policy_locations = {
            r.location
            for r in policy_rows
            if r.location not in {"workspace", "temp"}
        }
        locations = sorted(file_locations | policy_locations)
        return [
            ShareEntry(
                location=loc,
                read_only=(loc == "uploads"),
                has_policy=(loc in policy_locations),
            )
            for loc in locations
        ]
