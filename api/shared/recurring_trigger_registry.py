"""Registry for shared recurring PlatformJob trigger definitions.

Each operation type registers param validation; admission lives in the
operation's own module (reviews first). The registry is generic storage
policy, not quality domain logic.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.services.cron_parser import get_schedule_zone, is_cron_expression_valid

OPERATION_AGENT_REVIEW = "agent_review"
OPERATION_AGENT_EVALUATION_SUITE = "agent_evaluation_suite"

_LOOKBACK_MIN_DAYS = 1
_LOOKBACK_MAX_DAYS = 30
_LOOKBACK_DEFAULT_DAYS = 7
_MAX_MATRIX_IDS = 10


class TriggerDefinitionError(ValueError):
    """Invalid trigger configuration; safe to surface as a 422 detail."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class TriggerRequesterError(Exception):
    """Stored schedule requester fails fire-time validation."""

    def __init__(self, code: str = "requester_unauthorized") -> None:
        super().__init__(code)
        self.code = code


def validate_cron_and_timezone(cron_expression: str, timezone_name: str) -> None:
    if not cron_expression or not cron_expression.strip():
        raise TriggerDefinitionError("invalid_cron", "cron_expression cannot be blank.")
    if not is_cron_expression_valid(cron_expression.strip()):
        raise TriggerDefinitionError(
            "invalid_cron", "cron_expression must be a valid 5-field cron expression."
        )
    try:
        get_schedule_zone(timezone_name)
    except ValueError as exc:
        raise TriggerDefinitionError("invalid_timezone", str(exc)) from exc


def validate_overlap_policy(overlap_policy: str) -> None:
    if overlap_policy != "skip":
        raise TriggerDefinitionError(
            "overlap_unsupported",
            "Only overlap_policy='skip' is supported; queue/replace are rejected.",
        )


def validate_review_params(params: dict[str, Any]) -> dict[str, Any]:
    """Validate agent_review operation params into canonical form."""
    if not isinstance(params, dict):
        raise TriggerDefinitionError("invalid_params", "operation_params must be an object.")
    raw_review_id = params.get("review_id")
    try:
        review_id = UUID(str(raw_review_id))
    except (TypeError, ValueError) as exc:
        raise TriggerDefinitionError(
            "invalid_review", "operation_params.review_id must be a UUID."
        ) from exc
    raw_lookback = params.get("lookback_days", _LOOKBACK_DEFAULT_DAYS)
    if isinstance(raw_lookback, bool) or not isinstance(raw_lookback, int):
        raise TriggerDefinitionError(
            "invalid_lookback", "operation_params.lookback_days must be an integer."
        )
    lookback_days = raw_lookback
    if not (_LOOKBACK_MIN_DAYS <= lookback_days <= _LOOKBACK_MAX_DAYS):
        raise TriggerDefinitionError(
            "invalid_lookback",
            f"operation_params.lookback_days must be {_LOOKBACK_MIN_DAYS}-{_LOOKBACK_MAX_DAYS}.",
        )
    return {"review_id": str(review_id), "lookback_days": lookback_days}


def validate_trigger_definition(
    *,
    operation_type: str,
    operation_params: dict[str, Any] | None,
    cron_expression: str,
    timezone_name: str,
    overlap_policy: str,
) -> dict[str, Any]:
    """Validate a trigger definition; returns canonical operation params."""
    validate_cron_and_timezone(cron_expression, timezone_name)
    validate_overlap_policy(overlap_policy)
    if operation_type == OPERATION_AGENT_REVIEW:
        return validate_review_params(operation_params or {})
    if operation_type == OPERATION_AGENT_EVALUATION_SUITE:
        return validate_suite_params(operation_params or {})
    raise TriggerDefinitionError(
        "unknown_operation", f"Unknown operation_type: {operation_type}."
    )


def _uuid_list(raw: Any, field: str, *, min_items: int, max_items: int) -> list[str]:
    if not isinstance(raw, list):
        raise TriggerDefinitionError(
            "invalid_params", f"operation_params.{field} must be a list."
        )
    if not (min_items <= len(raw) <= max_items):
        raise TriggerDefinitionError(
            "invalid_params",
            f"operation_params.{field} must hold {min_items}-{max_items} items.",
        )
    try:
        return [str(UUID(str(item))) for item in raw]
    except (TypeError, ValueError) as exc:
        raise TriggerDefinitionError(
            "invalid_params", f"operation_params.{field} must hold UUIDs."
        ) from exc


def validate_suite_params(params: dict[str, Any]) -> dict[str, Any]:
    """Validate agent_evaluation_suite operation params into canonical form."""
    from src.services.agent_evaluations.quotas import MAX_REPETITIONS_PER_CASE

    if not isinstance(params, dict):
        raise TriggerDefinitionError("invalid_params", "operation_params must be an object.")
    try:
        suite_id = str(UUID(str(params.get("suite_id"))))
    except (TypeError, ValueError) as exc:
        raise TriggerDefinitionError(
            "invalid_suite", "operation_params.suite_id must be a UUID."
        ) from exc
    candidate_ids = _uuid_list(
        params.get("candidate_ids", []), "candidate_ids", min_items=0, max_items=_MAX_MATRIX_IDS
    )
    profile_ids = _uuid_list(
        params.get("profile_ids", []), "profile_ids", min_items=1, max_items=_MAX_MATRIX_IDS
    )
    raw_repetitions = params.get("repetitions_override")
    repetitions: int | None = None
    if raw_repetitions is not None:
        if (
            isinstance(raw_repetitions, bool)
            or not isinstance(raw_repetitions, int)
            or not (1 <= raw_repetitions <= MAX_REPETITIONS_PER_CASE)
        ):
            raise TriggerDefinitionError(
                "invalid_repetitions",
                f"operation_params.repetitions_override must be 1-{MAX_REPETITIONS_PER_CASE}.",
            )
        repetitions = raw_repetitions
    return {
        "suite_id": suite_id,
        "candidate_ids": candidate_ids,
        "profile_ids": profile_ids,
        "repetitions_override": repetitions,
    }


async def load_trigger_requester(db, user_id: UUID):
    """Load the stored schedule requester as a UserPrincipal.

    Real, active, non-system users only; superusers are governed by each
    operation's own authorization, same as their on-demand callers.
    """
    from src.core.principal import UserPrincipal
    from src.models.orm.users import User

    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.is_system:
        raise TriggerRequesterError("requester_unauthorized")
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
