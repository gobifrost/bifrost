"""Workspace export/import HTTP adapters."""

import io
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from shared import export_import as service
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.export_import import BulkExportRequest, ImportResult
from src.services.authorization.enforce import load_caller, require_operation, GLOBAL

router = APIRouter(prefix="/api/export-import", tags=["Export/Import"])


class ExportRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


async def _caller(db, user, *, operation: str):
    caller = await load_caller(db, user)
    require_operation(caller, operation, GLOBAL)
    return caller


def _download(content: bytes, filename: str, media_type: str) -> StreamingResponse:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem, suffix = filename.rsplit(".", 1)
    return StreamingResponse(io.BytesIO(content), media_type=media_type,
                             headers={"Content-Disposition": f'attachment; filename="{stem}_{timestamp}.{suffix}"'})


@router.post("/export/knowledge")
async def export_knowledge(request: ExportRequest, db: DbSession, user: CurrentActiveUser) -> StreamingResponse:
    caller = await _caller(db, user, operation="POST /api/export-import/export/knowledge")
    result = await service._build_knowledge_export(db, caller, request.ids or None)
    return _download(result.model_dump_json(indent=2).encode(), "knowledge_export.json", "application/json")


@router.post("/export/configs")
async def export_configs(request: ExportRequest, db: DbSession, user: CurrentActiveUser) -> StreamingResponse:
    caller = await _caller(db, user, operation="POST /api/export-import/export/configs")
    result = await service._build_configs_export(db, caller, request.ids or None)
    return _download(result.model_dump_json(indent=2).encode(), "configs_export.json", "application/json")


@router.post("/export/tables")
async def export_tables(request: ExportRequest, db: DbSession, user: CurrentActiveUser) -> StreamingResponse:
    caller = await _caller(db, user, operation="POST /api/export-import/export/tables")
    result = await service._build_tables_export(db, caller, request.ids or None)
    return _download(result.model_dump_json(indent=2).encode(), "tables_export.json", "application/json")


@router.post("/export/integrations")
async def export_integrations(request: ExportRequest, db: DbSession, user: CurrentActiveUser) -> StreamingResponse:
    caller = await _caller(db, user, operation="POST /api/export-import/export/integrations")
    result = await service._build_integrations_export(db, caller, request.ids or None)
    return _download(result.model_dump_json(indent=2).encode(), "integrations_export.json", "application/json")


@router.post("/export/all")
async def export_all(request: BulkExportRequest, db: DbSession, user: CurrentActiveUser) -> StreamingResponse:
    caller = await _caller(db, user, operation="POST /api/export-import/export/all")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as content:
        for entity, ids in (("knowledge", request.knowledge_ids), ("tables", request.table_ids),
                            ("configs", request.config_ids), ("integrations", request.integration_ids)):
            result = await getattr(service, f"_build_{entity}_export")(db, caller, ids or None)
            if result.items:
                content.writestr(f"{entity}.json", result.model_dump_json(indent=2))
    return _download(archive.getvalue(), "bifrost_export.zip", "application/zip")


@router.post("/import/knowledge")
async def import_knowledge(
    db: DbSession, user: CurrentActiveUser, file: UploadFile = File(...),
    replace_existing: bool = Form(True),
    target_organization_id: str | None = Form(None),
) -> ImportResult:
    caller = await _caller(db, user, operation="POST /api/export-import/import/knowledge")
    return await service.import_knowledge(db, caller, await file.read(), replace_existing,
                                       target_organization_id)


@router.post("/import/tables")
async def import_tables(
    db: DbSession, user: CurrentActiveUser, file: UploadFile = File(...),
    replace_existing: bool = Form(True),
    target_organization_id: str | None = Form(None),
) -> ImportResult:
    caller = await _caller(db, user, operation="POST /api/export-import/import/tables")
    return await service.import_tables(db, caller, await file.read(), replace_existing,
                                       target_organization_id)


@router.post("/import/configs")
async def import_configs(
    db: DbSession, user: CurrentActiveUser, file: UploadFile = File(...),
    replace_existing: bool = Form(True),
    source_secret_key: str | None = Form(None),
    target_organization_id: str | None = Form(None),
) -> ImportResult:
    caller = await _caller(db, user, operation="POST /api/export-import/import/configs")
    return await service.import_configs(db, caller, await file.read(), replace_existing,
                                       source_secret_key, target_organization_id)


@router.post("/import/integrations")
async def import_integrations(
    db: DbSession, user: CurrentActiveUser, file: UploadFile = File(...),
    replace_existing: bool = Form(True),
    source_secret_key: str | None = Form(None),
    target_organization_id: str | None = Form(None),
) -> ImportResult:
    caller = await _caller(db, user, operation="POST /api/export-import/import/integrations")
    return await service.import_integrations(db, caller, await file.read(), replace_existing,
                                       source_secret_key, target_organization_id)


@router.post("/import/all")
async def import_all(
    db: DbSession, user: CurrentActiveUser, file: UploadFile = File(...),
    replace_existing: bool = Form(True),
    source_secret_key: str | None = Form(None),
    target_organization_id: str | None = Form(None),
) -> dict:
    caller = await _caller(db, user, operation="POST /api/export-import/import/all")
    return await service.import_all(db, caller, await file.read(), replace_existing,
                                       source_secret_key, target_organization_id)
