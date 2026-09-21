"""Admission and read helpers for on-demand agent reviews."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_reviews import (
    AgentReviewServiceError,
    assert_review_sources_readable,
    authorize_review_agent,
    build_review_evidence_input,
    freeze_review_profile_snapshot,
    profile_snapshot_to_dict,
    review_request_fingerprint,
)
from shared.models import (
    AgentReviewDefinitionCreate,
    AgentReviewDefinitionPublic,
    AgentReviewDefinitionUpdate,
    AgentReviewRunCreate,
    AgentReviewRunPublic,
    AgentReviewRunResults,
    AgentReviewVersionCreate,
    AgentReviewVersionPublic,
    AgentReviewDefinitionsPage,
    AgentReviewVersionsPage,
    AgentReviewJobPayload,
)
from shared.agent_finding_queries import serialize_finding
from src.core.principal import UserPrincipal
from src.models.orm.agent_findings import AgentFinding
from shared.agent_finding_visibility import visible_agent_finding_condition
from src.models.orm.agent_reviews import AgentReviewDefinition, AgentReviewRun, AgentReviewVersion
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import enqueue_platform_job


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _domain_error(code: str, detail: str = "Review not found.", http_status: int = 404) -> NoReturn:
    raise AgentReviewServiceError(code, public_detail=detail, http_status=http_status)


def _trim_required(value: str, field: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        _domain_error("invalid_review_input", f"{field} cannot be blank.", 422)
    return trimmed


def _trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


async def _ensure_profile_policy(user: UserPrincipal, profile_id: UUID | None) -> None:
    if profile_id is not None and not user.is_superuser:
        _domain_error("profile_forbidden", "Explicit review profile selection requires a platform administrator.", 403)


async def _load_agent_for_review(db: AsyncSession, user: UserPrincipal, agent_id: UUID) -> Agent:
    org_id = user.organization_id if not user.is_superuser else None
    await authorize_review_agent(db, user, agent_id=agent_id, org_id=org_id)
    agent = await db.get(Agent, agent_id)
    if agent is None or not agent.is_active:
        _domain_error("agent_not_found", "Agent not found.")
    return agent


async def _resolve_review_org(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    agent: Agent,
    organization_id: UUID | None,
    organization_id_set: bool,
) -> UUID | None:
    if not user.is_superuser:
        if user.organization_id is None:
            _domain_error("agent_not_found", "Agent not found.")
        if organization_id_set and organization_id != user.organization_id:
            _domain_error("review_org_forbidden", "Review organization scope is not allowed.", 403)
        return user.organization_id
    if agent.organization_id is not None:
        if organization_id_set and organization_id != agent.organization_id:
            _domain_error("review_org_mismatch", "Review organization must match the agent organization.", 422)
        return agent.organization_id
    if organization_id is None:
        return None
    if await db.get(Organization, organization_id) is None:
        _domain_error("organization_not_found", "Organization not found.")
    return organization_id


async def _review_or_404(db: AsyncSession, user: UserPrincipal, review_id: UUID, *, lock: bool = False) -> AgentReviewDefinition:
    stmt = select(AgentReviewDefinition).where(AgentReviewDefinition.id == review_id)
    if lock:
        stmt = stmt.with_for_update()
    review = (await db.execute(stmt)).scalar_one_or_none()
    if review is None:
        _domain_error("review_not_found", "Review not found.")
    try:
        await _load_agent_for_review(db, user, review.agent_id)
    except AgentReviewServiceError as exc:
        raise AgentReviewServiceError("review_not_found", public_detail="Review not found.") from exc
    if not user.is_superuser and review.org_id != user.organization_id:
        _domain_error("review_not_found", "Review not found.")
    return review


async def _latest_version(db: AsyncSession, review: AgentReviewDefinition) -> AgentReviewVersion:
    version = (await db.execute(
        select(AgentReviewVersion).where(
            AgentReviewVersion.review_id == review.id,
            AgentReviewVersion.version == review.latest_version,
        )
    )).scalar_one_or_none()
    if version is None:
        _domain_error("review_version_missing", "Review latest version is missing.", 409)
    return version


def _definition_public(review: AgentReviewDefinition, version: AgentReviewVersion) -> AgentReviewDefinitionPublic:
    return AgentReviewDefinitionPublic(
        id=review.id,
        agent_id=review.agent_id,
        org_id=review.org_id,
        name=review.name,
        status=review.status,
        latest_version=review.latest_version,
        latest_version_id=version.id,
        latest_version_created_at=version.created_at,
        created_by=review.created_by,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def _version_public(version: AgentReviewVersion) -> AgentReviewVersionPublic:
    return AgentReviewVersionPublic.model_validate(version)


async def create_review_definition(db: AsyncSession, *, user: UserPrincipal, body: AgentReviewDefinitionCreate) -> AgentReviewDefinitionPublic:
    principal = user
    await _ensure_profile_policy(principal, body.model_profile_id)
    agent = await _load_agent_for_review(db, principal, body.agent_id)
    org_id = await _resolve_review_org(
        db,
        user=principal,
        agent=agent,
        organization_id=body.organization_id,
        organization_id_set="organization_id" in body.model_fields_set,
    )
    now = datetime.now(timezone.utc)
    review = AgentReviewDefinition(
        id=uuid4(),
        agent_id=agent.id,
        org_id=org_id,
        name=_trim_required(body.name, "name"),
        status="active",
        latest_version=1,
        created_by=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    version = AgentReviewVersion(
        id=uuid4(),
        review_id=review.id,
        version=1,
        review_statement=_trim_required(body.review_statement, "review_statement"),
        evidence_format_instructions=_trim_optional(body.evidence_format_instructions),
        model_profile_id=body.model_profile_id,
        created_by=principal.user_id,
        created_at=now,
    )
    db.add(review)
    await db.flush()
    db.add(version)
    await db.flush()
    return _definition_public(review, version)


async def list_review_definitions(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    agent_id: UUID,
    status_filter: str,
    organization_id: UUID | None,
    limit: int,
    offset: int,
) -> AgentReviewDefinitionsPage:
    principal = user
    await _load_agent_for_review(db, principal, agent_id)
    filters = [AgentReviewDefinition.agent_id == agent_id]
    if not principal.is_superuser:
        filters.append(AgentReviewDefinition.org_id == principal.organization_id)
    elif organization_id is not None:
        filters.append(AgentReviewDefinition.org_id == organization_id)
    if status_filter != "all":
        filters.append(AgentReviewDefinition.status == status_filter)
    total = int((await db.execute(select(func.count()).select_from(AgentReviewDefinition).where(*filters))).scalar() or 0)
    rows = (await db.execute(
        select(AgentReviewDefinition)
        .where(*filters)
        .order_by(AgentReviewDefinition.updated_at.desc(), AgentReviewDefinition.created_at.desc(), AgentReviewDefinition.id)
        .limit(limit)
        .offset(offset)
    )).scalars().all()
    items: list[AgentReviewDefinitionPublic] = []
    for row in rows:
        items.append(_definition_public(row, await _latest_version(db, row)))
    return AgentReviewDefinitionsPage(items=items, total=total, limit=limit, offset=offset)


async def get_review_definition(db: AsyncSession, *, user: UserPrincipal, review_id: UUID) -> AgentReviewDefinitionPublic:
    principal = user
    review = await _review_or_404(db, principal, review_id)
    return _definition_public(review, await _latest_version(db, review))


async def update_review_definition(db: AsyncSession, *, user: UserPrincipal, review_id: UUID, body: AgentReviewDefinitionUpdate) -> AgentReviewDefinitionPublic:
    principal = user
    review = await _review_or_404(db, principal, review_id, lock=True)
    if "name" in body.model_fields_set and body.name is not None:
        review.name = _trim_required(body.name, "name")
    if "status" in body.model_fields_set and body.status is not None:
        review.status = body.status
    review.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return _definition_public(review, await _latest_version(db, review))


async def create_review_version(db: AsyncSession, *, user: UserPrincipal, review_id: UUID, body: AgentReviewVersionCreate) -> AgentReviewVersionPublic:
    principal = user
    await _ensure_profile_policy(principal, body.model_profile_id)
    review = await _review_or_404(db, principal, review_id, lock=True)
    if review.status != "active":
        _domain_error("review_disabled", "Disabled reviews cannot be versioned.", 409)
    next_version = review.latest_version + 1
    version = AgentReviewVersion(
        id=uuid4(),
        review_id=review.id,
        version=next_version,
        review_statement=_trim_required(body.review_statement, "review_statement"),
        evidence_format_instructions=_trim_optional(body.evidence_format_instructions),
        model_profile_id=body.model_profile_id,
        created_by=principal.user_id,
    )
    review.latest_version = next_version
    review.updated_at = datetime.now(timezone.utc)
    db.add(version)
    await db.flush()
    return _version_public(version)


async def list_review_versions(db: AsyncSession, *, user: UserPrincipal, review_id: UUID, limit: int, offset: int) -> AgentReviewVersionsPage:
    principal = user
    review = await _review_or_404(db, principal, review_id)
    total = int((await db.execute(select(func.count()).select_from(AgentReviewVersion).where(AgentReviewVersion.review_id == review.id))).scalar() or 0)
    rows = (await db.execute(
        select(AgentReviewVersion)
        .where(AgentReviewVersion.review_id == review.id)
        .order_by(AgentReviewVersion.version.desc())
        .limit(limit)
        .offset(offset)
    )).scalars().all()
    return AgentReviewVersionsPage(items=[_version_public(row) for row in rows], total=total, limit=limit, offset=offset)


def _run_public(run: AgentReviewRun) -> AgentReviewRunPublic:
    return AgentReviewRunPublic.model_validate(run)


async def admit_agent_review_run(db: AsyncSession, *, user: UserPrincipal, review_id: UUID, body: AgentReviewRunCreate) -> tuple[AgentReviewRun, PlatformJob, bool]:
    from src.jobs.platform.agent_review import AGENT_REVIEW_DEFINITION

    principal = user
    review = await _review_or_404(db, principal, review_id, lock=True)
    if review.status != "active":
        _domain_error("review_disabled", "Disabled reviews cannot run.", 409)
    version = await _latest_version(db, review)
    await _ensure_profile_policy(principal, version.model_profile_id)
    profile = await freeze_review_profile_snapshot(db, profile_id=version.model_profile_id)
    evidence = await build_review_evidence_input(
        db,
        principal,
        review=review,
        version=version,
        requested_run_ids=body.run_ids,
    )
    request_fingerprint = review_request_fingerprint(
        review_id=review.id,
        review_version_id=version.id,
        review_version=version.version,
        selected_run_ids=evidence.selected_run_ids,
        review_input=evidence.input,
        source_refs=evidence.source_refs,
        profile_fingerprint=profile.fingerprint,
    )
    dedupe_material = {
        "requester": str(principal.user_id),
        "org_id": str(review.org_id) if review.org_id else None,
        "review_id": str(review.id),
        "review_version_id": str(version.id),
        "request_fingerprint": request_fingerprint,
    }
    dedupe_key = "agent-review:" + _hash(dedupe_material)
    review_run_id = uuid4()
    job, reused = await enqueue_platform_job(
        db,
        AGENT_REVIEW_DEFINITION,
        AgentReviewJobPayload(review_run_id=review_run_id),
        dedupe_key=dedupe_key,
        organization_id=review.org_id,
        requested_by_user_id=principal.user_id,
        requested_by_email=principal.email,
        requested_by_name=principal.name or principal.email or "Unknown",
        resource_type="agent_review_run",
        resource_id=str(review_run_id),
        title=f"Agent review: {review.name}",
        action_url=None,
    )
    if reused:
        existing = (await db.execute(select(AgentReviewRun).where(AgentReviewRun.platform_job_id == job.id))).scalar_one_or_none()
        payload = job.payload if isinstance(job.payload, dict) else {}
        if (
            existing is None
            or payload.get("review_run_id") != str(existing.id)
            or job.resource_type != "agent_review_run"
            or job.resource_id != str(existing.id)
            or job.organization_id != review.org_id
            or job.requested_by_user_id != str(principal.user_id)
            or existing.requested_by_user_id != principal.user_id
            or existing.review_id != review.id
            or existing.review_version_id != version.id
            or existing.review_version != version.version
            or existing.agent_id != review.agent_id
            or existing.org_id != review.org_id
            or existing.request_fingerprint != request_fingerprint
        ):
            _domain_error("review_job_invariant", "Agent review job is missing its domain record.", 409)
        try:
            await assert_review_sources_readable(db, principal, review_run=existing)
        except AgentReviewServiceError as exc:
            raise AgentReviewServiceError("review_run_not_found", public_detail="Review run not found.") from exc
        return existing, job, True
    run = AgentReviewRun(
        id=review_run_id,
        review_id=review.id,
        review_version_id=version.id,
        review_version=version.version,
        agent_id=review.agent_id,
        org_id=review.org_id,
        platform_job_id=job.id,
        requested_by_user_id=principal.user_id,
        requested_run_ids=list(body.run_ids),
        selected_run_ids=evidence.selected_run_ids,
        source_evidence=evidence.input,
        source_refs=evidence.source_refs,
        profile_snapshot=profile_snapshot_to_dict(profile),
        profile_fingerprint=profile.fingerprint,
        request_fingerprint=request_fingerprint,
        input_bytes=evidence.input_bytes,
    )
    db.add(run)
    await db.flush()
    return run, job, False


async def get_review_run(db: AsyncSession, *, user: UserPrincipal, review_run_id: UUID) -> AgentReviewRunPublic:
    principal = user
    run = await db.get(AgentReviewRun, review_run_id)
    if run is None:
        _domain_error("review_run_not_found", "Review run not found.")
    try:
        await assert_review_sources_readable(db, principal, review_run=run)
    except AgentReviewServiceError as exc:
        raise AgentReviewServiceError("review_run_not_found", public_detail="Review run not found.") from exc
    return _run_public(run)


async def get_review_results(db: AsyncSession, *, user: UserPrincipal, review_run_id: UUID) -> AgentReviewRunResults:
    principal = user
    run = await db.get(AgentReviewRun, review_run_id)
    if run is None:
        _domain_error("review_run_not_found", "Review run not found.")
    try:
        await assert_review_sources_readable(db, principal, review_run=run)
    except AgentReviewServiceError as exc:
        raise AgentReviewServiceError("review_run_not_found", public_detail="Review run not found.") from exc
    findings = (await db.execute(
        select(AgentFinding)
        .where(
            AgentFinding.source_review_run_id == run.id,
            visible_agent_finding_condition(principal),
        )
        .order_by(AgentFinding.source_ordinal, AgentFinding.id)
    )).scalars().all()
    items = []
    for f in findings:
        items.append(await serialize_finding(db, f, principal))
    return AgentReviewRunResults(
        review_run=_run_public(run),
        findings=items,
        usage_operation_id=run.id,
    )
