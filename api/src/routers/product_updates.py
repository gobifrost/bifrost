"""Platform-admin Product Updates feed and durable presentation receipts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from src.core.auth import CurrentActiveUser
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
from src.services.authorization.enforce import load_caller, require_operation
from src.services.authorization.evaluator import GLOBAL

router = APIRouter(
    prefix="/api/product-updates",
    tags=["Product Updates"],
)

@router.get("", response_model=ProductUpdatesFeedResponse, **operation_route("productupdates.get"))
async def get_product_updates(
    user: CurrentActiveUser,
    db: DbSession,
) -> ProductUpdatesFeedResponse:
    """Return this build's approved bundle and the caller's prior receipts."""
    caller = await load_caller(db, user)
    require_operation(caller, "productupdates.get", GLOBAL)
    if caller.ctx is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to view settings")
    bundle = get_running_product_updates_bundle()
    visible_ids = visible_entry_ids(bundle)
    receipts = ProductUpdatesReceiptService(db)
    return ProductUpdatesFeedResponse(
        bundle=bundle,
        seen_entry_ids=await receipts.seen_entry_ids(caller.ctx.user_id, visible_ids),
    )


@router.post(
    "/receipts",
    response_model=ProductUpdatesReceiptResponse,
    **operation_route("productupdates.receipts.create"),
)
async def create_product_update_receipts(
    request: ProductUpdatesReceiptRequest,
    user: CurrentActiveUser,
    db: DbSession,
) -> ProductUpdatesReceiptResponse:
    """Persist receipt(s) for entries presented from the current bundle only."""
    caller = await load_caller(db, user)
    require_operation(caller, "productupdates.receipts.create", GLOBAL)
    if caller.ctx is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to manage settings")
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
    await receipts.record_presented(caller.ctx.user_id, requested_ids)
    return ProductUpdatesReceiptResponse(
        seen_entry_ids=await receipts.seen_entry_ids(caller.ctx.user_id, visible_ids)
    )
