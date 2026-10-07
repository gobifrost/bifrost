"""Platform-admin Product Updates feed and durable presentation receipts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from src.core.auth import CurrentSuperuser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.models.contracts.product_updates import (
    ProductUpdatesFeedResponse,
    ProductUpdatesReceiptRequest,
    ProductUpdatesReceiptResponse,
)
from src.services.operation_catalog import operation_route
from src.services.product_updates import (
    ProductUpdatesReceiptService,
    get_running_product_updates_bundle,
    visible_entry_ids,
)

router = APIRouter(
    prefix="/api/product-updates",
    tags=["Product Updates"],
    dependencies=[RequirePlatformAdmin],
)

@router.get("", response_model=ProductUpdatesFeedResponse, **operation_route("productupdates.get"))
async def get_product_updates(
    user: CurrentSuperuser,
    db: DbSession,
) -> ProductUpdatesFeedResponse:
    """Return this build's approved bundle and the caller's prior receipts."""
    bundle = get_running_product_updates_bundle()
    visible_ids = visible_entry_ids(bundle)
    receipts = ProductUpdatesReceiptService(db)
    return ProductUpdatesFeedResponse(
        bundle=bundle,
        seen_entry_ids=await receipts.seen_entry_ids(user.user_id, visible_ids),
    )


@router.post(
    "/receipts",
    response_model=ProductUpdatesReceiptResponse,
    **operation_route("productupdates.receipts.create"),
)
async def create_product_update_receipts(
    request: ProductUpdatesReceiptRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> ProductUpdatesReceiptResponse:
    """Persist receipt(s) for entries presented from the current bundle only."""
    bundle = get_running_product_updates_bundle()
    visible_ids = visible_entry_ids(bundle)
    requested_ids = set(request.entry_ids)
    unknown_ids = requested_ids - visible_ids
    if unknown_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entry_ids must all belong to the running Product Updates bundle",
        )

    receipts = ProductUpdatesReceiptService(db)
    await receipts.record_presented(user.user_id, requested_ids)
    return ProductUpdatesReceiptResponse(
        seen_entry_ids=await receipts.seen_entry_ids(user.user_id, visible_ids)
    )
