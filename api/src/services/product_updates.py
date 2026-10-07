"""Read the build-pinned Product Updates bundle and persist presentation receipts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.contracts.product_updates import ProductUpdatesBundle
from src.models.orm.product_updates import ProductUpdateReceipt


def load_product_updates_bundle(path: Path) -> ProductUpdatesBundle:
    """Load one approved bundle without consulting mutable database content."""
    with path.open(encoding="utf-8") as bundle_file:
        return ProductUpdatesBundle.model_validate(json.load(bundle_file))


@lru_cache(maxsize=1)
def get_running_product_updates_bundle() -> ProductUpdatesBundle:
    """Return the immutable bundle mounted into this running API image."""
    return load_product_updates_bundle(get_settings().product_updates_bundle_path)


def visible_entry_ids(bundle: ProductUpdatesBundle) -> set[UUID]:
    """Return entries that this build can present in the app shell."""
    return {entry.id for entry in bundle.entries if entry.in_app is not False}


class ProductUpdatesReceiptService:
    """Persist admin presentations while keeping all content in the bundle."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def seen_entry_ids(
        self, admin_id: UUID, visible_ids: set[UUID]
    ) -> list[str]:
        if not visible_ids:
            return []
        result = await self.db.execute(
            select(ProductUpdateReceipt.entry_id).where(
                ProductUpdateReceipt.admin_id == admin_id,
                ProductUpdateReceipt.entry_id.in_(visible_ids),
            )
        )
        return [str(entry_id) for entry_id in result.scalars()]

    async def record_presented(self, admin_id: UUID, entry_ids: set[UUID]) -> None:
        await self.db.execute(
            insert(ProductUpdateReceipt)
            .values([{"admin_id": admin_id, "entry_id": entry_id} for entry_id in entry_ids])
            .on_conflict_do_nothing(index_elements=["admin_id", "entry_id"])
        )
        await self.db.commit()
