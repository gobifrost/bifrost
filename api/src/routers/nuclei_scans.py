"""
Nuclei scan ingestion and finding lifecycle router.

Phase 2 scope:
- ingest JSON findings for a scan run
- dedupe/re-alert suppression against open/acknowledged findings
- resolve findings no longer present in subsequent scans
- list scan history and findings
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, select

from src.core.auth import CurrentSuperuser
from src.core.database import DbSession
from src.models.orm.tables import Document, Table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scans", tags=["Nuclei Scans"])

RUNS_TABLE = "nuclei_scan_runs"
FINDINGS_TABLE = "nuclei_findings"

FINDING_STATES = {"open", "acknowledged", "resolved", "suppressed"}
SEVERITIES = {"critical", "high", "medium", "low", "info"}


class FindingInput(BaseModel):
    template_id: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    severity: str = Field(..., description="critical|high|medium|low|info")
    matched_at: datetime
    template_tags: list[str] = Field(default_factory=list)
    title: str | None = None
    description: str | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SEVERITIES:
            raise ValueError(f"Invalid severity: {value}")
        return normalized


class ScanIngestRequest(BaseModel):
    scan_host_device_id: str
    findings: list[FindingInput] = Field(default_factory=list)
    incomplete: bool = False
    scan_started_at: datetime | None = None
    scan_completed_at: datetime | None = None


class ScanRunCreateRequest(BaseModel):
    scan_host_device_id: str
    target_list: list[str] = Field(default_factory=list)


class BulkStateUpdateRequest(BaseModel):
    finding_ids: list[str] = Field(default_factory=list)
    state: Literal["acknowledged", "suppressed", "resolved"]


async def _get_or_create_table(db: DbSession, *, org_id: UUID, name: str) -> Table:
    table = await _get_table(db, org_id=org_id, name=name)
    if table:
        return table

    table = Table(
        name=name,
        organization_id=org_id,
        description=f"Auto-created table for {name}",
        schema=None,
        created_by="system",
    )
    db.add(table)
    await db.flush()
    await db.refresh(table)
    return table


async def _get_table(db: DbSession, *, org_id: UUID, name: str) -> Table | None:
    result = await db.execute(
        select(Table).where(
            and_(
                Table.organization_id == org_id,
                Table.name == name,
            )
        )
    )
    return result.scalar_one_or_none()


def _occurrence_key(org_id: UUID, finding: FindingInput) -> str:
    return (
        f"{org_id}:{finding.template_id}:{finding.host}:"
        f"{finding.matched_at.astimezone(timezone.utc).isoformat()}"
    )


def _stable_id(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post(
    "/runs/{org_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Create a scan run record",
)
async def create_scan_run(
    org_id: UUID,
    request: ScanRunCreateRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> dict[str, Any]:
    runs_table = await _get_or_create_table(db, org_id=org_id, name=RUNS_TABLE)
    run_id = _stable_id(f"{org_id}:{request.scan_host_device_id}:{datetime.now(timezone.utc).isoformat()}")

    run_doc = Document(
        id=run_id,
        table_id=runs_table.id,
        data={
            "run_id": run_id,
            "org_id": str(org_id),
            "status": "queued",
            "scan_host_device_id": request.scan_host_device_id,
            "target_list": request.target_list,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "finding_counts": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            "incomplete": False,
        },
        created_by=user.email,
        updated_by=user.email,
    )
    db.add(run_doc)
    await db.flush()

    return {"run_id": run_id, "status": "queued"}


@router.post(
    "/runs/{org_id}/{run_id}/ingest",
    summary="Ingest findings and update lifecycle state",
)
async def ingest_scan_results(
    org_id: UUID,
    run_id: str,
    request: ScanIngestRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> dict[str, Any]:
    runs_table = await _get_or_create_table(db, org_id=org_id, name=RUNS_TABLE)
    findings_table = await _get_or_create_table(db, org_id=org_id, name=FINDINGS_TABLE)

    run_result = await db.execute(
        select(Document).where(
            and_(Document.table_id == runs_table.id, Document.id == run_id)
        )
    )
    run_doc = run_result.scalar_one_or_none()
    if not run_doc:
        raise HTTPException(status_code=404, detail="Scan run not found")

    findings_result = await db.execute(select(Document).where(Document.table_id == findings_table.id))
    existing_docs = list(findings_result.scalars().all())

    existing_by_occurrence: dict[str, Document] = {}
    active_by_template_host: dict[tuple[str, str], Document] = {}

    for doc in existing_docs:
        data = doc.data or {}
        key = data.get("occurrence_key")
        if key:
            existing_by_occurrence[key] = doc

        if data.get("state") in {"open", "acknowledged"}:
            tpl = str(data.get("template_id") or "")
            host = str(data.get("host") or "")
            if tpl and host:
                active_by_template_host[(tpl, host)] = doc

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    net_new = 0
    realert_suppressed = 0

    current_open_keys: set[str] = set()
    scanned_hosts: set[str] = set()

    for finding in request.findings:
        counts[finding.severity] += 1
        scanned_hosts.add(finding.host)
        occurrence_key = _occurrence_key(org_id, finding)
        finding_id = _stable_id(occurrence_key)

        existing = existing_by_occurrence.get(occurrence_key)
        is_realert = (finding.template_id, finding.host) in active_by_template_host
        if is_realert:
            realert_suppressed += 1

        payload = {
            "finding_id": finding_id,
            "run_id": run_id,
            "org_id": str(org_id),
            "template_id": finding.template_id,
            "host": finding.host,
            "severity": finding.severity,
            "state": "open" if not is_realert else active_by_template_host[(finding.template_id, finding.host)].data.get("state", "open"),
            "matched_at": finding.matched_at.astimezone(timezone.utc).isoformat(),
            "template_tags": finding.template_tags,
            "title": finding.title,
            "description": finding.description,
            "occurrence_key": occurrence_key,
            "is_realert_suppressed": is_realert,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if existing:
            existing.data = {**(existing.data or {}), **payload}
            existing.updated_by = user.email
        else:
            if not is_realert:
                net_new += 1
            doc = Document(
                id=finding_id,
                table_id=findings_table.id,
                data={
                    **payload,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                created_by=user.email,
                updated_by=user.email,
            )
            db.add(doc)
            existing_by_occurrence[occurrence_key] = doc

        current_open_keys.add(f"{finding.template_id}:{finding.host}")

    resolved = 0
    if not request.incomplete:
        now_iso = datetime.now(timezone.utc).isoformat()
        for doc in existing_docs:
            data = doc.data or {}
            if data.get("state") not in {"open", "acknowledged"}:
                continue

            host = str(data.get("host") or "")
            template_id = str(data.get("template_id") or "")
            if not host or not template_id:
                continue

            if host in scanned_hosts and f"{template_id}:{host}" not in current_open_keys:
                doc.data = {
                    **data,
                    "state": "resolved",
                    "resolved_at": now_iso,
                    "updated_at": now_iso,
                }
                doc.updated_by = user.email
                resolved += 1

    run_data = run_doc.data or {}
    if request.scan_completed_at:
        scan_completed_at = request.scan_completed_at.astimezone(timezone.utc).isoformat()
    elif request.incomplete:
        scan_completed_at = run_data.get("scan_completed_at")
    else:
        scan_completed_at = datetime.now(timezone.utc).isoformat()

    run_doc.data = {
        **run_data,
        "status": "completed" if not request.incomplete else "incomplete",
        "scan_host_device_id": request.scan_host_device_id,
        "scan_started_at": request.scan_started_at.astimezone(timezone.utc).isoformat() if request.scan_started_at else run_data.get("scan_started_at"),
        "scan_completed_at": scan_completed_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "finding_counts": counts,
        "incomplete": request.incomplete,
        "net_new": net_new,
        "resolved": resolved,
        "realert_suppressed": realert_suppressed,
        "total_findings": len(request.findings),
    }
    run_doc.updated_by = user.email

    await db.flush()

    return {
        "run_id": run_id,
        "status": run_doc.data["status"],
        "counts": counts,
        "net_new": net_new,
        "resolved": resolved,
        "realert_suppressed": realert_suppressed,
        "incomplete": request.incomplete,
    }


@router.get("/history/{org_id}", summary="List scan runs for an organization")
async def get_scan_history(
    org_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    runs_table = await _get_table(db, org_id=org_id, name=RUNS_TABLE)
    if not runs_table:
        return {"items": [], "total": 0}

    result = await db.execute(select(Document).where(Document.table_id == runs_table.id))
    docs = list(result.scalars().all())

    items = [doc.data for doc in docs if isinstance(doc.data, dict)]
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return {"items": items[:limit], "total": len(items)}


@router.get("/findings/{org_id}", summary="List findings for an organization")
async def get_findings(
    org_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
    state: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    template_tag: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    if state and state not in FINDING_STATES:
        raise HTTPException(status_code=400, detail=f"Invalid state: {state}")
    if severity and severity.lower() not in SEVERITIES:
        raise HTTPException(status_code=400, detail=f"Invalid severity: {severity}")

    findings_table = await _get_table(db, org_id=org_id, name=FINDINGS_TABLE)
    if not findings_table:
        return {"items": [], "total": 0}

    result = await db.execute(select(Document).where(Document.table_id == findings_table.id))
    docs = list(result.scalars().all())

    items: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.data if isinstance(doc.data, dict) else {}
        if state and data.get("state") != state:
            continue
        if severity and str(data.get("severity", "")).lower() != severity.lower():
            continue
        if template_tag and template_tag not in (data.get("template_tags") or []):
            continue
        items.append(data)

    items.sort(key=lambda x: x.get("matched_at", ""), reverse=True)
    return {"items": items[:limit], "total": len(items)}


@router.post("/findings/{org_id}/bulk-state", summary="Bulk update finding state")
async def bulk_update_finding_state(
    org_id: UUID,
    request: BulkStateUpdateRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> dict[str, Any]:
    findings_table = await _get_or_create_table(db, org_id=org_id, name=FINDINGS_TABLE)

    if not request.finding_ids:
        return {"updated": 0}

    result = await db.execute(
        select(Document).where(
            and_(
                Document.table_id == findings_table.id,
                Document.id.in_(request.finding_ids),
            )
        )
    )
    docs = list(result.scalars().all())

    now = datetime.now(timezone.utc).isoformat()
    for doc in docs:
        updates: dict[str, Any] = {
            "state": request.state,
            "updated_at": now,
        }
        if request.state == "resolved":
            updates["resolved_at"] = now
        doc.data = {**(doc.data or {}), **updates}
        doc.updated_by = user.email

    await db.flush()
    return {"updated": len(docs), "state": request.state}
