"""Shared service helpers for on-demand agent review execution."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.agent_recorded_evidence import (
    RecordedEvidenceOversized,
    load_recorded_run_evidence,
)
from shared.quality_usage import (
    begin_quality_usage_attempt,
    mark_quality_usage_unobserved,
    record_quality_usage_observation,
)
from src.core.database import get_db_context
from src.core.principal import UserPrincipal
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobContext, PlatformJobFailure
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import (
    MAX_REVIEW_INPUT_BYTES,
    MAX_REVIEW_RUNS,
    AgentReviewDefinition,
    AgentReviewRun,
    AgentReviewVersion,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.users import User
from src.services.execution.agent_run_access import agent_run_visibility_conditions
from src.services.llm.base import LLMConfig, LLMMessage, LLMResponse
from src.services.llm.factory import get_llm_config
from src.services.model_pricing import canonical_provider
from src.services.platform_jobs import lock_running_platform_job_for_result_write

MAX_REVIEW_SUMMARY_CHARS = 2000
MAX_REVIEW_FINDINGS = 20
MAX_REVIEW_DESCRIPTION_CHARS = 4000
MAX_REVIEW_EXPECTED_CHARS = 4000
MAX_REVIEW_EVIDENCE_MARKDOWN_CHARS = 20000


class AgentReviewServiceError(Exception):
    def __init__(
        self,
        code: str,
        public_detail: str = "Review run not found.",
        http_status: int = 404,
    ) -> None:
        super().__init__(public_detail)
        self.code = code
        self.public_detail = public_detail
        self.http_status = http_status


@dataclass(frozen=True)
class ReviewProfileSnapshot:
    profile_id: UUID
    profile_name: str | None
    provider: str
    model: str
    endpoint: str | None
    openai_transport: str | None
    anthropic_prompt_cache_supported: bool | None
    default_max_tokens: int | None
    extra_params: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True)
class ReviewEvidenceInput:
    input: dict[str, Any]
    source_refs: list[dict[str, Any]]
    selected_run_ids: list[UUID]
    input_bytes: int
    request_fingerprint: str


@dataclass(frozen=True)
class ReviewFindingDraft:
    ordinal: int
    finding_kind: Literal["problem", "opportunity"]
    description: str
    expected_behavior: str | None
    evidence_markdown: str | None
    source_run_ids: list[UUID]


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def review_request_fingerprint(
    *,
    review_id: UUID,
    review_version_id: UUID,
    review_version: int,
    selected_run_ids: Sequence[UUID],
    review_input: dict[str, Any],
    source_refs: list[dict[str, Any]],
    profile_fingerprint: str,
) -> str:
    return _hash(
        {
            "review_id": str(review_id),
            "review_version_id": str(review_version_id),
            "review_version": review_version,
            "selected_run_ids": [str(item) for item in _unique_sorted(selected_run_ids)],
            "input": review_input,
            "source_refs": source_refs,
            "profile_fingerprint": profile_fingerprint,
        }
    )


def _unique_sorted(ids: Sequence[UUID]) -> list[UUID]:
    return sorted(set(ids), key=str)


def _profile_snapshot_dict(snapshot: ReviewProfileSnapshot) -> dict[str, Any]:
    return {
        "profile_id": str(snapshot.profile_id),
        "profile_name": snapshot.profile_name,
        "provider": snapshot.provider,
        "model": snapshot.model,
        "endpoint": snapshot.endpoint,
        "openai_transport": snapshot.openai_transport,
        "anthropic_prompt_cache_supported": snapshot.anthropic_prompt_cache_supported,
        "default_max_tokens": snapshot.default_max_tokens,
        "extra_params": snapshot.extra_params,
        "fingerprint": snapshot.fingerprint,
    }


def profile_snapshot_to_dict(snapshot: ReviewProfileSnapshot) -> dict[str, Any]:
    return _profile_snapshot_dict(snapshot)


def _profile_snapshot_from_mapping(raw: Any) -> ReviewProfileSnapshot:
    if not isinstance(raw, dict):
        raise AgentReviewServiceError("profile_unavailable")
    try:
        profile_id = UUID(str(raw["profile_id"]))
        provider = _nonempty_str(raw.get("provider"), "provider")
        model = _nonempty_str(raw.get("model"), "model")
        fingerprint = _nonempty_str(raw.get("fingerprint"), "fingerprint")
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentReviewServiceError("profile_unavailable") from exc
    extra_params = raw.get("extra_params") or {}
    if not isinstance(extra_params, dict):
        raise AgentReviewServiceError("profile_unavailable")
    return ReviewProfileSnapshot(
        profile_id=profile_id,
        profile_name=raw.get("profile_name") if raw.get("profile_name") is None or isinstance(raw.get("profile_name"), str) else None,
        provider=provider,
        model=model,
        endpoint=raw.get("endpoint") if raw.get("endpoint") is None or isinstance(raw.get("endpoint"), str) else None,
        openai_transport=raw.get("openai_transport") if raw.get("openai_transport") is None or isinstance(raw.get("openai_transport"), str) else None,
        anthropic_prompt_cache_supported=raw.get("anthropic_prompt_cache_supported") if raw.get("anthropic_prompt_cache_supported") is None or isinstance(raw.get("anthropic_prompt_cache_supported"), bool) else None,
        default_max_tokens=raw.get("default_max_tokens") if raw.get("default_max_tokens") is None or isinstance(raw.get("default_max_tokens"), int) else None,
        extra_params=dict(extra_params),
        fingerprint=fingerprint,
    )


def _nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


async def _entity_access_allowed(entity: Any, user: UserPrincipal, db: AsyncSession) -> bool:
    if user.is_superuser:
        return True
    if user.organization_id is None:
        return False
    if getattr(entity, "organization_id", None) not in (None, user.organization_id):
        return False
    raw_level = getattr(entity, "access_level", "authenticated")
    level = getattr(raw_level, "value", str(raw_level)).lower()
    if level == "everyone":
        return True
    if level == "authenticated":
        return not user.is_external
    if level == "private":
        return getattr(entity, "owner_user_id", None) == user.user_id
    if level == "role_based":
        from shared.role_cache import get_user_roles

        role_ids, _ = await get_user_roles(user.user_id, db)
        return bool(
            set(role_ids)
            & {getattr(role, "id", None) for role in (getattr(entity, "roles", []) or [])}
        )
    return False


async def authorize_review_agent(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    agent_id: UUID,
    org_id: UUID | None,
) -> None:
    if not user.is_superuser and (org_id is None or org_id != user.organization_id):
        raise AgentReviewServiceError("review_unauthorized")
    agent = (
        await db.execute(
            select(Agent).options(selectinload(Agent.roles)).where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if agent is None or not agent.is_active:
        raise AgentReviewServiceError("review_unauthorized")
    if not user.is_superuser and agent.organization_id not in (None, org_id):
        raise AgentReviewServiceError("review_unauthorized")
    if not await _entity_access_allowed(agent, user, db):
        raise AgentReviewServiceError("review_unauthorized")


def _collect_delegated_source_ids(evidence: dict[str, Any]) -> list[UUID]:
    ids: list[UUID] = []

    def append(raw: Any) -> None:
        try:
            item = UUID(str(raw))
        except (TypeError, ValueError):
            return
        if item not in ids:
            ids.append(item)

    append(evidence.get("run_id"))

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        append(node.get("run_id"))
        for child in node.get("children") or []:
            visit(child)

    delegation = evidence.get("delegation") or {}
    if isinstance(delegation, dict):
        for child in delegation.get("children") or []:
            visit(child)
    return ids


async def _freeze_source_refs(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    source_ids: Sequence[UUID],
    org_id: UUID | None,
) -> list[dict[str, Any]]:
    ids = _unique_sorted(source_ids)
    if not ids:
        raise AgentReviewServiceError("source_run_not_found")
    rows = (
        await db.execute(
            select(AgentRun).where(AgentRun.id.in_(ids), *agent_run_visibility_conditions(user))
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(ids):
        raise AgentReviewServiceError("source_run_not_found")
    refs: list[dict[str, Any]] = []
    for run_id in ids:
        row = by_id[run_id]
        if row.org_id != org_id:
            raise AgentReviewServiceError("source_run_not_found")
        refs.append(
            {
                "run_id": str(row.id),
                "agent_id": str(row.agent_id) if row.agent_id else None,
                "org_id": str(row.org_id) if row.org_id else None,
                "root_run_id": str(row.root_run_id) if row.root_run_id else None,
                "parent_run_id": str(row.parent_run_id) if row.parent_run_id else None,
                "trigger_type": row.trigger_type,
            }
        )
    return refs


async def build_review_evidence_input(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    review: AgentReviewDefinition,
    version: AgentReviewVersion,
    requested_run_ids: Sequence[UUID],
) -> ReviewEvidenceInput:
    selected_run_ids = _unique_sorted(requested_run_ids)
    if not selected_run_ids or len(selected_run_ids) > MAX_REVIEW_RUNS:
        raise AgentReviewServiceError("source_run_not_found", http_status=422)
    selected_runs: list[dict[str, Any]] = []
    all_source_refs: list[dict[str, Any]] = []
    frozen_ids: set[UUID] = set()
    for run_id in selected_run_ids:
        try:
            projection = await load_recorded_run_evidence(db, run_id, user=user)
        except RecordedEvidenceOversized as exc:
            raise AgentReviewServiceError("review_input_oversized", http_status=413) from exc
        except Exception as exc:
            raise AgentReviewServiceError("source_run_not_found") from exc
        evidence = projection.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise AgentReviewServiceError("source_run_not_found")
        if projection.get("completeness", {}).get("terminal_status") is not True:
            raise AgentReviewServiceError("source_evidence_incomplete", http_status=422)
        selected_row = await db.get(AgentRun, run_id)
        if selected_row is None or selected_row.trigger_type == "evaluation_synthetic":
            raise AgentReviewServiceError("source_run_not_found")
        if selected_row.agent_id != review.agent_id:
            raise AgentReviewServiceError("source_run_wrong_agent", http_status=422)
        if selected_row.org_id != review.org_id:
            raise AgentReviewServiceError("source_run_not_found")
        source_ids = [run_id, *_collect_delegated_source_ids(evidence)]
        source_refs = await _freeze_source_refs(
            db,
            user,
            source_ids=source_ids,
            org_id=review.org_id,
        )
        for ref in source_refs:
            ref_id = UUID(str(ref["run_id"]))
            if ref_id not in frozen_ids:
                frozen_ids.add(ref_id)
                all_source_refs.append(ref)
        selected_runs.append(
            {
                "run_id": str(run_id),
                "evidence": evidence,
                "completeness": projection.get("completeness") or {},
                "evidence_refs": list(projection.get("evidence_refs") or []),
                "limitations": list(projection.get("limitations") or []),
            }
        )
    review_input = {
        "review_statement": version.review_statement,
        "evidence_format_instructions": version.evidence_format_instructions,
        "selected_runs": selected_runs,
        "response_schema": {
            "summary": "string or null, maximum 2000 characters",
            "findings": [
                {
                    "kind": "problem or opportunity",
                    "description": "non-empty string, maximum 4000 characters",
                    "expected_behavior": "string or null, maximum 4000 characters",
                    "evidence_markdown": "string or null, maximum 20000 characters",
                    "source_run_ids": "non-empty array of run_id values from selected_runs",
                }
            ],
        },
        "instructions": (
            "Return JSON only with keys summary and findings. Each finding object must "
            "include kind, description, expected_behavior, evidence_markdown, and "
            "source_run_ids. Findings must cite source_run_ids from selected_runs. "
            "Missing or incomplete evidence cannot prove an absent action. Do not fetch "
            "links or perform external actions."
        ),
    }
    encoded = _canonical_json(review_input).encode("utf-8")
    if len(encoded) > MAX_REVIEW_INPUT_BYTES:
        raise AgentReviewServiceError("review_input_oversized", http_status=413)
    return ReviewEvidenceInput(
        input=review_input,
        source_refs=all_source_refs,
        selected_run_ids=selected_run_ids,
        input_bytes=len(encoded),
        request_fingerprint=review_request_fingerprint(
            review_id=review.id,
            review_version_id=version.id,
            review_version=version.version,
            selected_run_ids=selected_run_ids,
            review_input=review_input,
            source_refs=all_source_refs,
            profile_fingerprint="",
        ),
    )


async def _profile_and_config(
    db: AsyncSession, *, profile_id: UUID | None
) -> tuple[AIModelProfile, LLMConfig]:
    if profile_id is None:
        assignment = (
            await db.execute(
                select(AIModelAssignment)
                .options(selectinload(AIModelAssignment.profile).selectinload(AIModelProfile.connection))
                .where(AIModelAssignment.assignment_key == "testing")
            )
        ).scalar_one_or_none()
        if assignment is None:
            raise AgentReviewServiceError("profile_unavailable", http_status=422)
        profile = assignment.profile
        if profile is None:
            raise AgentReviewServiceError("profile_unavailable", http_status=422)
        config = await get_llm_config(db, profile_id=profile.id)
        return profile, config
    profile = (
        await db.execute(
            select(AIModelProfile)
            .options(selectinload(AIModelProfile.connection))
            .where(AIModelProfile.id == profile_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise AgentReviewServiceError("profile_unavailable", http_status=422)
    config = await get_llm_config(db, profile_id=profile_id)
    return profile, config


async def freeze_review_profile_snapshot(
    db: AsyncSession,
    *,
    profile_id: UUID | None,
) -> ReviewProfileSnapshot:
    profile, config = await _profile_and_config(db, profile_id=profile_id)
    return _snapshot_from_profile_config(profile, config)


def _snapshot_from_profile_config(profile: AIModelProfile, config: LLMConfig) -> ReviewProfileSnapshot:
    material = {
        "profile_id": str(profile.id),
        "profile_name": profile.name,
        "provider": config.provider,
        "model": config.model,
        "endpoint": config.endpoint,
        "openai_transport": config.openai_transport,
        "anthropic_prompt_cache_supported": config.anthropic_prompt_cache_supported,
        "default_max_tokens": config.default_max_tokens,
        "extra_params": config.extra_params,
    }
    return ReviewProfileSnapshot(
        profile_id=profile.id,
        profile_name=profile.name,
        provider=config.provider,
        model=config.model,
        endpoint=config.endpoint,
        openai_transport=config.openai_transport,
        anthropic_prompt_cache_supported=config.anthropic_prompt_cache_supported,
        default_max_tokens=config.default_max_tokens,
        extra_params=dict(config.extra_params or {}),
        fingerprint=_hash(material),
    )


async def _validate_profile_snapshot(db: AsyncSession, snapshot: ReviewProfileSnapshot) -> LLMConfig:
    profile, config = await _profile_and_config(db, profile_id=snapshot.profile_id)
    current = _snapshot_from_profile_config(profile, config)
    if _profile_snapshot_dict(current) != _profile_snapshot_dict(snapshot):
        raise AgentReviewServiceError("profile_drift")
    return config


async def assert_review_sources_readable(
    db: AsyncSession,
    user: UserPrincipal,
    *,
    review_run: AgentReviewRun,
) -> None:
    review = await db.get(AgentReviewDefinition, review_run.review_id)
    version = await db.get(AgentReviewVersion, review_run.review_version_id)
    agent = await db.get(Agent, review_run.agent_id)
    if (
        review is None
        or version is None
        or agent is None
        or review.id != review_run.review_id
        or version.review_id != review.id
        or review.agent_id != review_run.agent_id
        or review.org_id != review_run.org_id
        or review_run.review_version != version.version
    ):
        raise AgentReviewServiceError("source_run_not_found")
    if not user.is_superuser and (review_run.org_id is None or review_run.org_id != user.organization_id):
        raise AgentReviewServiceError("source_run_not_found")
    try:
        await authorize_review_agent(
            db,
            user,
            agent_id=review_run.agent_id,
            org_id=review_run.org_id,
        )
    except AgentReviewServiceError as exc:
        raise AgentReviewServiceError("source_run_not_found") from exc
    source_evidence = review_run.source_evidence if isinstance(review_run.source_evidence, dict) else None
    if source_evidence is None:
        raise AgentReviewServiceError("source_run_not_found")
    actual_input_bytes = len(_canonical_json(source_evidence).encode("utf-8"))
    if (
        review_run.input_bytes is None
        or review_run.input_bytes != actual_input_bytes
        or actual_input_bytes > MAX_REVIEW_INPUT_BYTES
    ):
        raise AgentReviewServiceError("source_run_not_found")
    if not isinstance(review_run.source_refs, list) or not review_run.source_refs:
        raise AgentReviewServiceError("source_run_not_found")
    required_ref_keys = {
        "run_id",
        "agent_id",
        "org_id",
        "root_run_id",
        "parent_run_id",
        "trigger_type",
    }
    frozen: dict[UUID, dict[str, Any]] = {}
    for ref in review_run.source_refs:
        if not isinstance(ref, dict) or set(ref) != required_ref_keys:
            raise AgentReviewServiceError("source_run_not_found")
        try:
            run_id = UUID(str(ref["run_id"]))
        except (TypeError, ValueError) as exc:
            raise AgentReviewServiceError("source_run_not_found") from exc
        if run_id in frozen:
            raise AgentReviewServiceError("source_run_not_found")
        frozen[run_id] = ref
    for run_id in review_run.selected_run_ids or []:
        if run_id not in frozen:
            raise AgentReviewServiceError("source_run_not_found")
    rows = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.id.in_(list(frozen)), *agent_run_visibility_conditions(user)
            )
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(frozen):
        raise AgentReviewServiceError("source_run_not_found")
    selected_set = set(review_run.selected_run_ids or [])
    for run_id, ref in frozen.items():
        row = by_id[run_id]
        if row.org_id != review_run.org_id:
            raise AgentReviewServiceError("source_run_not_found")
        if run_id in selected_set and row.agent_id != review_run.agent_id:
            raise AgentReviewServiceError("source_run_not_found")
        if (str(row.agent_id) if row.agent_id else None) != ref["agent_id"]:
            raise AgentReviewServiceError("source_run_not_found")
        if (str(row.org_id) if row.org_id else None) != ref["org_id"]:
            raise AgentReviewServiceError("source_run_not_found")
        if (str(row.root_run_id) if row.root_run_id else None) != ref["root_run_id"]:
            raise AgentReviewServiceError("source_run_not_found")
        if (str(row.parent_run_id) if row.parent_run_id else None) != ref["parent_run_id"]:
            raise AgentReviewServiceError("source_run_not_found")
        if row.trigger_type != ref["trigger_type"]:
            raise AgentReviewServiceError("source_run_not_found")


def parse_review_response(raw: Any, *, selected_run_ids: Sequence[UUID]) -> tuple[str | None, list[ReviewFindingDraft]]:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentReviewServiceError("invalid_review_response") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise AgentReviewServiceError("invalid_review_response")
    summary = data.get("summary")
    if summary is not None:
        if not isinstance(summary, str) or len(summary) > MAX_REVIEW_SUMMARY_CHARS:
            raise AgentReviewServiceError("invalid_review_response")
    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list) or len(raw_findings) > MAX_REVIEW_FINDINGS:
        raise AgentReviewServiceError("invalid_review_response")
    allowed = {UUID(str(item)) for item in selected_run_ids}
    drafts: list[ReviewFindingDraft] = []
    for ordinal, item in enumerate(raw_findings):
        if not isinstance(item, dict):
            raise AgentReviewServiceError("invalid_review_response")
        kind = item.get("kind")
        if kind not in ("problem", "opportunity"):
            raise AgentReviewServiceError("invalid_review_response")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > MAX_REVIEW_DESCRIPTION_CHARS:
            raise AgentReviewServiceError("invalid_review_response")
        expected = item.get("expected_behavior")
        if expected is not None and (not isinstance(expected, str) or len(expected) > MAX_REVIEW_EXPECTED_CHARS):
            raise AgentReviewServiceError("invalid_review_response")
        evidence_md = item.get("evidence_markdown")
        if evidence_md is not None and (not isinstance(evidence_md, str) or len(evidence_md) > MAX_REVIEW_EVIDENCE_MARKDOWN_CHARS):
            raise AgentReviewServiceError("invalid_review_response")
        raw_source_ids = item.get("source_run_ids")
        if not isinstance(raw_source_ids, list) or not raw_source_ids:
            raise AgentReviewServiceError("invalid_review_response")
        try:
            source_ids = [UUID(str(value)) for value in raw_source_ids]
        except (TypeError, ValueError) as exc:
            raise AgentReviewServiceError("invalid_review_response") from exc
        if len(set(source_ids)) != len(source_ids) or not set(source_ids).issubset(allowed):
            raise AgentReviewServiceError("invalid_review_response")
        drafts.append(
            ReviewFindingDraft(
                ordinal=ordinal,
                finding_kind=kind,
                description=description.strip(),
                expected_behavior=expected,
                evidence_markdown=evidence_md,
                source_run_ids=source_ids,
            )
        )
    return summary, drafts


def usage_from_response(response: LLMResponse, *, duration_ms: int) -> dict[str, Any] | None:
    def valid_count(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    input_tokens = valid_count(response.input_tokens)
    output_tokens = valid_count(response.output_tokens)
    cache_read = valid_count(response.cache_read_tokens)
    cache_write = valid_count(response.cache_write_tokens)
    if input_tokens is None or output_tokens is None or cache_read is None or cache_write is None:
        return None
    provider_cost = response.provider_cost
    if provider_cost is not None and not isinstance(provider_cost, Decimal):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "provider_cost": provider_cost,
        "duration_ms": duration_ms,
    }


async def _call_review_model(config: LLMConfig, review_input: dict[str, Any]) -> tuple[LLMResponse, int]:
    from src.services.llm.pydantic_client import PydanticAIClient

    client = PydanticAIClient(config)
    started = time.monotonic()
    response = await client.complete(
        [
            LLMMessage(
                role="system",
                content=(
                    "You review recorded production agent evidence. Return only JSON "
                    "matching the response_schema in the user payload. Required finding "
                    "fields are kind, description, expected_behavior, evidence_markdown, "
                    "and source_run_ids. Do not call tools or fetch links."
                ),
            ),
            LLMMessage(role="user", content=_canonical_json(review_input)),
        ]
    )
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    return response, duration_ms


def _principal_from_user(user: User) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )


def _failure(code: str, message: str = "Agent review failed.") -> PlatformJobFailure:
    return PlatformJobFailure(code, message, retryable=False)


async def _load_and_validate_pre_call(
    db: AsyncSession,
    context: PlatformJobContext,
    review_run_id: UUID,
) -> tuple[AgentReviewRun, AgentReviewDefinition, AgentReviewVersion, User, ReviewProfileSnapshot, LLMConfig, str, str]:
    fence = await lock_running_platform_job_for_result_write(
        db, job_id=context.job_id, lease_token=context.lease_token
    )
    if fence is None:
        raise PlatformJobCancelled
    job = await db.get(PlatformJob, context.job_id)
    if (
        job is None
        or job.job_type != "agent.review"
        or job.organization_id != context.organization_id
        or job.requested_by_user_id != context.requested_by_user_id
    ):
        raise _failure("job_mismatch")
    payload = job.payload if isinstance(job.payload, dict) else {}
    if str(payload.get("review_run_id")) != str(review_run_id):
        raise _failure("job_mismatch")
    review_run = await db.get(AgentReviewRun, review_run_id)
    if review_run is None or review_run.platform_job_id != context.job_id:
        raise _failure("review_not_found")
    if review_run.org_id != context.organization_id:
        raise _failure("job_mismatch")
    try:
        context_user_id = UUID(str(context.requested_by_user_id))
    except (TypeError, ValueError) as exc:
        raise _failure("requester_not_found") from exc
    if context_user_id != review_run.requested_by_user_id:
        raise _failure("requester_not_found")
    requester = await db.get(User, review_run.requested_by_user_id)
    if requester is None or not requester.is_active:
        raise _failure("requester_not_found")
    review = await db.get(AgentReviewDefinition, review_run.review_id)
    version = await db.get(AgentReviewVersion, review_run.review_version_id)
    if (
        review is None
        or version is None
        or review.status != "active"
        or version.review_id != review.id
        or review_run.review_version != version.version
        or review_run.agent_id != review.agent_id
        or review.org_id != review_run.org_id
    ):
        raise _failure("review_not_found")
    try:
        await authorize_review_agent(
            db,
            _principal_from_user(requester),
            agent_id=review_run.agent_id,
            org_id=review_run.org_id,
        )
    except AgentReviewServiceError as exc:
        raise AgentReviewServiceError("source_run_not_found") from exc
    await assert_review_sources_readable(
        db,
        _principal_from_user(requester),
        review_run=review_run,
    )
    snapshot = _profile_snapshot_from_mapping(review_run.profile_snapshot)
    if snapshot.fingerprint != review_run.profile_fingerprint:
        raise _failure("profile_drift")
    try:
        config = await _validate_profile_snapshot(db, snapshot)
    except AgentReviewServiceError as exc:
        raise _failure(exc.code) from exc
    request_fingerprint = review_run.request_fingerprint
    expected_fingerprint = review_request_fingerprint(
        review_id=review.id,
        review_version_id=version.id,
        review_version=version.version,
        selected_run_ids=review_run.selected_run_ids or [],
        review_input=dict(review_run.source_evidence or {}),
        source_refs=list(review_run.source_refs or []),
        profile_fingerprint=snapshot.fingerprint,
    )
    if request_fingerprint != expected_fingerprint:
        raise _failure("job_mismatch")
    idem_key = f"agent-review:{review_run.id}:review"
    return review_run, review, version, requester, snapshot, config, idem_key, request_fingerprint


async def _persist_findings(
    db: AsyncSession,
    review_run: AgentReviewRun,
    drafts: list[ReviewFindingDraft],
    *,
    summary: str | None,
) -> None:
    existing = (
        await db.execute(
            select(AgentFinding).where(AgentFinding.source_review_run_id == review_run.id)
        )
    ).scalars().all()
    by_ordinal = {row.source_ordinal: row for row in existing}
    draft_ordinals = {draft.ordinal for draft in drafts}
    if set(by_ordinal) - draft_ordinals:
        raise _failure("review_finding_conflict")
    for draft in drafts:
        source_run_refs = [
            ref for ref in (review_run.source_refs or [])
            if str(ref.get("run_id")) in {str(item) for item in draft.source_run_ids}
        ]
        if len(source_run_refs) != len(draft.source_run_ids):
            raise _failure("invalid_review_response")
        existing_row = by_ordinal.get(draft.ordinal)
        if existing_row is not None:
            if (
                existing_row.agent_id == review_run.agent_id
                and existing_row.org_id == review_run.org_id
                and existing_row.source_review_id == review_run.review_id
                and existing_row.source_review_version_id == review_run.review_version_id
                and existing_row.source_review_run_id == review_run.id
                and existing_row.source_review_version == review_run.review_version
                and existing_row.source_run_id == draft.source_run_ids[0]
                and existing_row.finding_kind == draft.finding_kind
                and existing_row.description == draft.description
                and existing_row.expected_behavior == draft.expected_behavior
                and existing_row.evidence_markdown == draft.evidence_markdown
                and existing_row.source_run_refs == source_run_refs
            ):
                continue
            raise _failure("review_finding_conflict")
        finding = AgentFinding(
            agent_id=review_run.agent_id,
            org_id=review_run.org_id,
            status="open",
            description=draft.description,
            expected_behavior=draft.expected_behavior,
            source_kind="run",
            source_run_id=draft.source_run_ids[0],
            finding_kind=draft.finding_kind,
            evidence_markdown=draft.evidence_markdown,
            source_review_id=review_run.review_id,
            source_review_version_id=review_run.review_version_id,
            source_review_run_id=review_run.id,
            source_review_version=review_run.review_version,
            source_run_refs=source_run_refs,
            source_ordinal=draft.ordinal,
            created_by=review_run.requested_by_user_id,
        )
        db.add(finding)
    review_run.result_summary = summary
    await db.flush()


async def execute_agent_review_job(context: PlatformJobContext, review_run_id: UUID) -> dict[str, Any]:
    async with get_db_context() as db:
        try:
            (
                review_run,
                _review,
                _version,
                _requester,
                snapshot,
                config,
                idem_key,
                request_fingerprint,
            ) = await _load_and_validate_pre_call(db, context, review_run_id)
        except AgentReviewServiceError as exc:
            raise _failure(exc.code) from exc
        accounting_provider = canonical_provider(snapshot.provider, snapshot.endpoint)
        attempt = await begin_quality_usage_attempt(
            db,
            idempotency_key=idem_key,
            quality_operation_type="agent_review",
            quality_operation_id=review_run.id,
            quality_operation_item_id="review",
            usage_purpose="agent_review",
            provider=accounting_provider,
            model=snapshot.model,
            request_fingerprint=request_fingerprint,
            organization_id=review_run.org_id,
            user_id=review_run.requested_by_user_id,
            platform_job_id=context.job_id,
            profile_id=snapshot.profile_id,
            profile_name=snapshot.profile_name,
            profile_fingerprint=snapshot.fingerprint,
        )
        if not attempt.created:
            raise _failure("review_attempt_already_started")
        review_input = dict(review_run.source_evidence or {})
        await db.commit()

    response: LLMResponse | None = None
    duration_ms = 0
    try:
        response, duration_ms = await _call_review_model(config, review_input)
    except Exception as exc:
        async with get_db_context() as db:
            await mark_quality_usage_unobserved(db, idempotency_key=idem_key, reason="provider_error")
            await db.commit()
        raise _failure("provider_error") from exc

    usage = usage_from_response(response, duration_ms=duration_ms)
    async with get_db_context() as db:
        if usage is None:
            await mark_quality_usage_unobserved(db, idempotency_key=idem_key, reason="missing_usage")
        else:
            await record_quality_usage_observation(
                db,
                idempotency_key=idem_key,
                quality_operation_type="agent_review",
                quality_operation_id=review_run_id,
                quality_operation_item_id="review",
                usage_purpose="agent_review",
                provider=accounting_provider,
                model=snapshot.model,
                request_fingerprint=request_fingerprint,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                provider_cost=usage["provider_cost"],
                duration_ms=usage["duration_ms"],
            )
        await db.commit()

    try:
        summary, drafts = parse_review_response(response.content, selected_run_ids=review_run.selected_run_ids)
    except AgentReviewServiceError as exc:
        raise _failure(exc.code) from exc

    async with get_db_context() as db:
        fence = await lock_running_platform_job_for_result_write(
            db, job_id=context.job_id, lease_token=context.lease_token
        )
        if fence is None:
            raise PlatformJobCancelled
        fresh = await db.get(AgentReviewRun, review_run_id)
        if fresh is None or fresh.platform_job_id != context.job_id or fresh.org_id != context.organization_id:
            raise _failure("job_mismatch")
        fresh_review = await db.get(AgentReviewDefinition, fresh.review_id)
        fresh_version = await db.get(AgentReviewVersion, fresh.review_version_id)
        if fresh_review is None or fresh_version is None:
            raise _failure("job_mismatch")
        if (
            fresh_review.id != _review.id
            or fresh_review.agent_id != review_run.agent_id
            or fresh_review.org_id != review_run.org_id
            or fresh_version.id != _version.id
            or fresh_version.review_id != fresh_review.id
            or fresh.review_version != review_run.review_version
            or fresh.agent_id != review_run.agent_id
            or fresh.org_id != review_run.org_id
            or fresh.requested_by_user_id != review_run.requested_by_user_id
            or fresh.profile_snapshot != review_run.profile_snapshot
            or fresh.profile_fingerprint != review_run.profile_fingerprint
            or fresh.source_evidence != review_run.source_evidence
            or fresh.source_refs != review_run.source_refs
            or fresh.selected_run_ids != review_run.selected_run_ids
            or fresh.request_fingerprint != request_fingerprint
        ):
            raise _failure("job_mismatch")
        expected_fingerprint = review_request_fingerprint(
            review_id=fresh_review.id,
            review_version_id=fresh_version.id,
            review_version=fresh_version.version,
            selected_run_ids=fresh.selected_run_ids or [],
            review_input=dict(fresh.source_evidence or {}),
            source_refs=list(fresh.source_refs or []),
            profile_fingerprint=snapshot.fingerprint,
        )
        if expected_fingerprint != request_fingerprint:
            raise _failure("job_mismatch")
        await _persist_findings(db, fresh, drafts, summary=summary)
        await db.commit()
    await context.report("Agent review complete", current=1, total=1, percent=100)
    return {"review_run_id": str(review_run_id), "findings_created": len(drafts)}
