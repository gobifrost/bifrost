"""Instance-wide agent package identity and normalized branding assets."""
from __future__ import annotations

import asyncio
import io
import re

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.logo_processing import process_logo
from shared.models import MCPPackageBranding, MCPPackageBrandingUpdate
from src.models.orm.config import SystemConfig

DEFAULT_PACKAGE_NAME = "Bifrost Agent"
LOGO_PATH = "/api/mcp/run/branding/logo"


def package_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def package_logo_png(content: bytes, content_type: str) -> bytes:
    """Use the shared validation and containment rules, then encode plugin PNG."""
    from PIL import Image

    processed = process_logo(content, content_type)
    with Image.open(io.BytesIO(processed.thumbnail_data)) as image:
        output = io.BytesIO()
        image.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS).save(
            output, "PNG", optimize=True,
        )
    return output.getvalue()


class MCPPackageBrandingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _row(self) -> SystemConfig | None:
        result = await self.session.execute(select(SystemConfig).where(
            SystemConfig.category == "mcp",
            SystemConfig.key == "package_branding",
            SystemConfig.organization_id.is_(None),
        ))
        return result.scalars().first()

    async def _ensure_row(self, actor: str) -> SystemConfig:
        # Serialize first-time name/logo writes so they share a single settings row.
        await self.session.execute(text("SELECT pg_advisory_xact_lock(640, 1)"))
        row = await self._row()
        if row is None:
            row = SystemConfig(category="mcp", key="package_branding", created_by=actor)
            self.session.add(row)
        row.updated_by = actor
        return row

    @staticmethod
    def _response(row: SystemConfig | None) -> MCPPackageBranding:
        name = (row.value_json or {}).get("name", DEFAULT_PACKAGE_NAME) if row else DEFAULT_PACKAGE_NAME
        return MCPPackageBranding(name=name, slug=package_slug(name),
                                  logo_url=LOGO_PATH if row and row.value_bytes else None)

    async def get(self) -> MCPPackageBranding:
        return self._response(await self._row())

    async def update(self, request: MCPPackageBrandingUpdate, actor: str) -> MCPPackageBranding:
        row = await self._ensure_row(actor)
        row.value_json = {"name": request.name}
        await self.session.flush()
        return self._response(row)

    async def logo(self) -> bytes | None:
        row = await self._row()
        return row.value_bytes if row else None

    async def upload_logo(self, content: bytes, content_type: str, actor: str) -> MCPPackageBranding:
        png = await asyncio.to_thread(package_logo_png, content, content_type)
        row = await self._ensure_row(actor)
        row.value_bytes = png
        await self.session.flush()
        return self._response(row)

    async def delete_logo(self, actor: str) -> MCPPackageBranding:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(640, 1)"))
        row = await self._row()
        if row:
            row.value_bytes = None
            row.updated_by = actor
            await self.session.flush()
        return self._response(row)
