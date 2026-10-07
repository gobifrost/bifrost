"""The running Product Updates bundle is admin-only and receipts are durable."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.models.orm import ProductUpdateReceipt
from src.services.product_updates import (
    ProductUpdatesReceiptService,
    get_running_product_updates_bundle,
    visible_entry_ids,
)


def _presentable_entry_id(feed: dict) -> str:
    return next(
        entry["id"] for entry in feed["bundle"]["entries"]
        if entry.get("in_app") is not False
    )


@pytest.mark.e2e
class TestProductUpdatesAPI:
    def test_admin_receipts_are_idempotent_and_limited_to_the_running_bundle(
        self, e2e_client, platform_admin, second_platform_admin
    ) -> None:
        feed_response = e2e_client.get(
            "/api/product-updates", headers=platform_admin.headers
        )
        assert feed_response.status_code == 200, feed_response.text
        feed = feed_response.json()
        assert feed["bundle"]["schema_version"] == 1
        assert isinstance(feed["seen_entry_ids"], list)

        entry_id = _presentable_entry_id(feed)
        receipt_response = e2e_client.post(
            "/api/product-updates/receipts",
            headers=platform_admin.headers,
            json={"entry_ids": [entry_id, entry_id]},
        )
        assert receipt_response.status_code == 200, receipt_response.text
        assert receipt_response.json()["seen_entry_ids"].count(entry_id) == 1

        repeated_response = e2e_client.post(
            "/api/product-updates/receipts",
            headers=platform_admin.headers,
            json={"entry_ids": [entry_id]},
        )
        assert repeated_response.status_code == 200, repeated_response.text
        assert repeated_response.json()["seen_entry_ids"].count(entry_id) == 1

        invalid_response = e2e_client.post(
            "/api/product-updates/receipts",
            headers=platform_admin.headers,
            json={"entry_ids": [str(uuid4())]},
        )
        assert invalid_response.status_code == 422

        hidden_entry_id = next(
            entry["id"] for entry in feed["bundle"]["entries"]
            if entry.get("in_app") is False
        )
        hidden_response = e2e_client.post(
            "/api/product-updates/receipts",
            headers=platform_admin.headers,
            json={"entry_ids": [hidden_entry_id]},
        )
        assert hidden_response.status_code == 422

        second_feed = e2e_client.get(
            "/api/product-updates", headers=second_platform_admin.headers
        )
        assert second_feed.status_code == 200, second_feed.text
        assert entry_id not in second_feed.json()["seen_entry_ids"]

        ownership_override = e2e_client.post(
            "/api/product-updates/receipts",
            headers=platform_admin.headers,
            json={
                "entry_ids": [entry_id],
                "admin_id": str(second_platform_admin.user_id),
            },
        )
        assert ownership_override.status_code == 422

    def test_non_admin_cannot_read_or_acknowledge_product_updates(
        self, e2e_client, org1_user
    ) -> None:
        read_response = e2e_client.get(
            "/api/product-updates", headers=org1_user.headers
        )
        assert read_response.status_code == 403

        receipt_response = e2e_client.post(
            "/api/product-updates/receipts",
            headers=org1_user.headers,
            json={"entry_ids": [str(uuid4())]},
        )
        assert receipt_response.status_code == 403


@pytest.mark.e2e
async def test_receipts_survive_bundle_revision_and_concurrent_duplicate_writes(
    async_session_factory, platform_admin
) -> None:
    """Only the permanent entry UUID keys a receipt, even across revisions."""
    bundle = get_running_product_updates_bundle()
    entry = next(entry for entry in bundle.entries if entry.in_app is not False)
    admin_id = platform_admin.user_id

    try:
        async with async_session_factory() as first, async_session_factory() as second:
            await asyncio.gather(
                ProductUpdatesReceiptService(first).record_presented(admin_id, {entry.id}),
                ProductUpdatesReceiptService(second).record_presented(admin_id, {entry.id}),
            )

        async with async_session_factory() as verify:
            persisted = await verify.scalar(
                select(func.count()).select_from(ProductUpdateReceipt).where(
                    ProductUpdateReceipt.admin_id == admin_id,
                    ProductUpdateReceipt.entry_id == entry.id,
                )
            )
            assert persisted == 1

            revised_bundle = bundle.model_copy(
                update={
                    "entries": [
                        item.model_copy(update={"revision": item.revision + 1})
                        if item.id == entry.id else item
                        for item in bundle.entries
                    ]
                }
            )
            seen = await ProductUpdatesReceiptService(verify).seen_entry_ids(
                admin_id, visible_entry_ids(revised_bundle)
            )
            assert str(entry.id) in seen
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(ProductUpdateReceipt).where(
                    ProductUpdateReceipt.admin_id == admin_id,
                    ProductUpdateReceipt.entry_id == entry.id,
                )
            )
            await cleanup.commit()
