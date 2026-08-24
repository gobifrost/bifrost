"""Reviewed Global Builder operation changesets for loose platform resources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pydantic_core
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.agents import AgentCreate, AgentUpdate
from src.models.contracts.applications import ApplicationUpdate
from src.models.contracts.forms import FormCreate, FormUpdate
from src.models.contracts.tables import TableCreate, TableUpdate
from src.models.contracts.workflows import WorkflowUpdateRequest
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agent_prompt_history import AgentPromptHistory
from src.models.orm.agents import AgentDelegation, Conversation
from src.models.orm.events import EventSubscription
from src.models.orm.executions import Execution
from src.models.orm.form_embed_secrets import FormEmbedSecret
from src.models.orm.form_publications import FormPublication
from src.models.orm.solution_builder import SolutionGlobalOperationChange
from src.models.orm.tables import Document
from src.services.audit import emit_audit
from src.services.operation_catalog import get_operation

if TYPE_CHECKING:
    from src.services.mcp_server.server import MCPContext


REVERSIBLE_AGENT_UPDATE_FIELDS = frozenset(
    {
        # Canonical Agent PUT can restore these fields through AgentUpdate today.
        # Nullable fields that the router ignores when set to null
        # (description and relationship/bundle fields)
        # are deliberately fail-closed until the Agent domain service supports
        # fully reversible/idempotent mutation.
        "name",
        "system_prompt",
        "channels",
        "access_level",
        "organization_id",
        "is_active",
        "knowledge_sources",
        "system_tools",
        "max_iterations",
        "max_token_budget",
    }
)
REVERSIBLE_FORM_UPDATE_FIELDS = frozenset(
    {
        "name",
        "description",
        "confirmation_markdown",
        "workflow_id",
        "launch_workflow_id",
        "default_launch_params",
        "allowed_query_params",
        "form_schema",
        "is_active",
        "access_level",
        "organization_id",
    }
)
REVERSIBLE_TABLE_UPDATE_FIELDS = frozenset(
    {
        "name",
        "description",
        "schema",
        "organization_id",
        "policies",
    }
)
REVERSIBLE_WORKFLOW_UPDATE_FIELDS = frozenset(
    {
        "organization_id",
        "access_level",
        "name",
        "display_name",
        "description",
        "category",
        "timeout_seconds",
        "execution_mode",
        "time_saved",
        "value",
        "tool_description",
        "cache_ttl_seconds",
        "tags",
    }
)
REVERSIBLE_APP_UPDATE_FIELDS = frozenset(
    {
        "name",
        "slug",
        "description",
        "icon",
        "access_level",
        "organization_id",
    }
)
SUPPORTED_GLOBAL_OPERATION_IDS = frozenset(
    {
        "agents.create",
        "agents.update",
        "apps.update",
        "forms.create",
        "forms.update",
        "tables.create",
        "tables.update",
        "workflows.update",
    }
)
REVIEWABLE_GLOBAL_OPERATION_STATES = ("staged", "failed")
PLANNED_GLOBAL_OPERATION_IDS = frozenset(
    {
        "agents.delete",
        "forms.delete",
        "tables.delete",
        "workflows.register",
        "workflows.delete",
        "apps.create",
        "apps.delete",
        "apps.publish",
    }
)


class GlobalOperationChangeError(RuntimeError):
    """One staged operation is invalid or cannot be applied safely."""


class GlobalOperationConflict(GlobalOperationChangeError):
    """Live state changed after the operation was reviewed."""


@dataclass(frozen=True, slots=True)
class GlobalOperationChangeResult:
    id: UUID
    operation_id: str
    resource_type: str
    resource_id: str | None
    state: str
    apply_job_id: UUID | None
    rollback_job_id: UUID | None
    validation_errors: list[str]
    before_state: dict[str, Any] | None
    before_fingerprint: str | None
    applied_state: dict[str, Any] | None
    applied_fingerprint: str | None
    applied_at: datetime | None
    payload: dict[str, Any]

    @classmethod
    def from_row(
        cls,
        row: SolutionGlobalOperationChange,
    ) -> "GlobalOperationChangeResult":
        return cls(
            id=row.id,
            operation_id=row.operation_id,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            state=row.state,
            apply_job_id=row.apply_job_id,
            rollback_job_id=row.rollback_job_id,
            validation_errors=[str(error) for error in row.validation_errors or []],
            before_state=row.before_state,
            before_fingerprint=row.before_fingerprint,
            applied_state=row.applied_state,
            applied_fingerprint=row.applied_fingerprint,
            applied_at=row.applied_at,
            payload=row.payload,
        )


def global_operation_inventory() -> dict[str, Any]:
    """Machine-readable status for Global loose-resource operation staging."""

    implemented = {
        operation_id: {
            "status": "implemented",
            "staging": "durable_changeset",
            "apply": "canonical_rest",
        }
        for operation_id in sorted(SUPPORTED_GLOBAL_OPERATION_IDS)
    }
    planned = {
        operation_id: {
            "status": "fail_closed",
            "reason": (
                "Global operation changeset adapter is not implemented for this "
                "domain yet; direct live writes are intentionally unavailable."
            ),
        }
        for operation_id in sorted(PLANNED_GLOBAL_OPERATION_IDS)
    }
    return {"implemented": implemented, "planned": planned}


def _fingerprint(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def operation_change_review_fingerprint(
    change: GlobalOperationChangeResult | SolutionGlobalOperationChange,
) -> str:
    """Stable fingerprint of the exact staged operation a human reviewed."""

    return (
        _fingerprint(
            {
                "operation_id": change.operation_id,
                "resource_type": change.resource_type,
                "resource_id": change.resource_id,
                "state": change.state,
                "payload": change.payload,
                "before_state": change.before_state,
                "before_fingerprint": getattr(change, "before_fingerprint", None),
                "validation_errors": change.validation_errors,
            }
        )
        or ""
    )


def operation_change_applied_fingerprint(
    change: GlobalOperationChangeResult | SolutionGlobalOperationChange,
) -> str:
    """Stable fingerprint of an applied operation a human approved for rollback."""

    return (
        _fingerprint(
            {
                "operation_id": change.operation_id,
                "resource_type": change.resource_type,
                "resource_id": change.resource_id,
                "state": change.state,
                "applied_state": getattr(change, "applied_state", None),
                "applied_fingerprint": getattr(change, "applied_fingerprint", None),
                "before_state": change.before_state,
                "before_fingerprint": getattr(change, "before_fingerprint", None),
                "apply_job_id": getattr(change, "apply_job_id", None),
            }
        )
        or ""
    )


def _jsonable(value: Any) -> Any:
    return pydantic_core.to_jsonable_python(value, fallback=str)


def _validate_model(model: type[BaseModel], payload: dict[str, Any]) -> dict[str, Any]:
    return _jsonable(model.model_validate(payload).model_dump(exclude_unset=True))


def _operation_resource_type(operation_id: str) -> str:
    return _adapter_for(operation_id).resource_type


def _payload_from_public(
    *,
    operation_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    adapter = _adapter_for(operation_id)
    return adapter.payload_from_public(state)


@dataclass(frozen=True, slots=True)
class GlobalOperationAdapter:
    operation_id: str
    resource_type: str
    create_model: type[BaseModel] | None
    update_model: type[BaseModel] | None
    get_path_template: str
    create_path: str
    update_method: str
    update_path_template: str
    delete_path_template: str
    reversible_update_fields: frozenset[str]
    create_label: str
    update_label: str

    @property
    def is_create(self) -> bool:
        return self.operation_id.endswith(".create")

    @property
    def is_update(self) -> bool:
        return self.operation_id.endswith(".update")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if (
            self.operation_id == "agents.create"
            and payload.get("role_ids")
            and payload.get("tool_ids")
        ):
            raise GlobalOperationChangeError(
                "agents.create cannot be safely staged for rollback with both "
                "role_ids and tool_ids; workflow role side effects are not reversible yet"
            )
        if self.operation_id == "forms.create" and payload.get("role_ids"):
            raise GlobalOperationChangeError(
                "forms.create cannot be safely staged for rollback with role_ids; "
                "workflow role side effects are not reversible yet"
            )
        if self.operation_id == "apps.update" and payload.get("organization_id") is not None:
            raise GlobalOperationChangeError(
                "apps.update cannot move Applications out of the Global boundary"
            )
        body = dict(payload)
        body["organization_id"] = None
        if self.is_create:
            if self.create_model is None:
                raise GlobalOperationChangeError(
                    f"{self.operation_id} has no create adapter"
                )
            return _validate_model(self.create_model, body)
        if self.is_update:
            unsupported = set(payload) - self.reversible_update_fields
            if unsupported:
                raise GlobalOperationChangeError(
                    f"{self.operation_id} cannot be safely staged for rollback with "
                    "unsupported field(s): "
                    + ", ".join(sorted(unsupported))
                )
            if self.update_model is None:
                raise GlobalOperationChangeError(
                    f"{self.operation_id} has no update adapter"
                )
            return _validate_model(self.update_model, body)
        raise GlobalOperationChangeError(
            f"{self.operation_id} is not implemented for Global operation changesets"
        )

    def payload_from_public(self, state: dict[str, Any]) -> dict[str, Any]:
        if not self.is_update:
            raise GlobalOperationChangeError(
                f"{self.operation_id} has no public rollback projection"
            )
        return self.validate_payload(
            {
                field: state.get(field)
                for field in self.reversible_update_fields
                if field in state
            }
        )

    async def fetch(
        self,
        context: "MCPContext",
        resource_id: str,
    ) -> dict[str, Any]:
        if self.operation_id == "apps.update":
            status, body = await _rest_json(context, "GET", "/api/applications")
            applications = body.get("applications") if isinstance(body, dict) else None
            if status == 200 and isinstance(applications, list):
                for application in applications:
                    if (
                        isinstance(application, dict)
                        and str(application.get("id")) == resource_id
                    ):
                        return application
            raise GlobalOperationChangeError(
                f"Could not read current {self.resource_type} {resource_id}: HTTP {status}: {body}"
            )
        status, body = await _rest_json(
            context,
            "GET",
            self.get_path_template.format(resource_id=resource_id),
        )
        if status != 200 or not isinstance(body, dict):
            raise GlobalOperationChangeError(
                f"Could not read current {self.resource_type} {resource_id}: HTTP {status}: {body}"
            )
        return body

    async def apply(
        self,
        context: "MCPContext",
        *,
        resource_id: str | None,
        payload: dict[str, Any],
        change_id: UUID,
    ) -> tuple[str, dict[str, Any]]:
        if self.is_create:
            status, body = await _rest_json(
                context,
                "POST",
                self.create_path,
                json_body=payload,
            )
            if status != 201 or not isinstance(body, dict):
                raise GlobalOperationChangeError(
                    f"{self.create_label} failed for operation {change_id}: HTTP {status}: {body}"
                )
            new_resource_id = str(body.get("id") or "")
            if not new_resource_id:
                raise GlobalOperationChangeError(
                    f"{self.create_label} returned no resource id for operation {change_id}"
                )
            return new_resource_id, body
        if self.is_update:
            if not resource_id:
                raise GlobalOperationChangeError("resource_id is required")
            status, body = await _rest_json(
                context,
                self.update_method,
                self.update_path_template.format(resource_id=resource_id),
                json_body=payload,
            )
            if status != 200 or not isinstance(body, dict):
                raise GlobalOperationChangeError(
                    f"{self.update_label} failed for operation {change_id}: HTTP {status}: {body}"
                )
            return resource_id, body
        raise GlobalOperationChangeError(
            f"{self.operation_id} has no apply adapter"
        )

    async def rollback(
        self,
        context: "MCPContext",
        *,
        resource_id: str | None,
        before_state: dict[str, Any] | None,
        change_id: UUID,
    ) -> None:
        if self.is_create:
            if not resource_id:
                raise GlobalOperationChangeError(
                    f"Operation {change_id} is missing rollback resource id"
                )
            if self.operation_id == "forms.create":
                status, body = await _rest_json(
                    context,
                    self.update_method,
                    self.update_path_template.format(resource_id=resource_id),
                    json_body={"is_active": False, "organization_id": None},
                )
                if status != 200 or not isinstance(body, dict):
                    raise GlobalOperationChangeError(
                        f"Rollback deactivate failed for operation {change_id}: HTTP {status}: {body}"
                    )
            status, body = await _rest_json(
                context,
                "DELETE",
                self.delete_path_template.format(resource_id=resource_id),
            )
            if status != 204:
                raise GlobalOperationChangeError(
                    f"Rollback delete failed for operation {change_id}: HTTP {status}: {body}"
                )
            return
        if self.is_update:
            if before_state is None or resource_id is None:
                raise GlobalOperationChangeError(
                    f"Operation {change_id} is missing rollback state"
                )
            status, body = await _rest_json(
                context,
                self.update_method,
                self.update_path_template.format(resource_id=resource_id),
                json_body=self.payload_from_public(before_state),
            )
            if status != 200 or not isinstance(body, dict):
                raise GlobalOperationChangeError(
                    f"Rollback update failed for operation {change_id}: HTTP {status}: {body}"
                )
            return
        raise GlobalOperationChangeError(
            f"{self.operation_id} has no rollback adapter"
        )


_GLOBAL_OPERATION_ADAPTERS: dict[str, GlobalOperationAdapter] = {
    "agents.create": GlobalOperationAdapter(
        operation_id="agents.create",
        resource_type="agent",
        create_model=AgentCreate,
        update_model=None,
        get_path_template="/api/agents/{resource_id}",
        create_path="/api/agents",
        update_method="PUT",
        update_path_template="/api/agents/{resource_id}",
        delete_path_template="/api/agents/{resource_id}",
        reversible_update_fields=REVERSIBLE_AGENT_UPDATE_FIELDS,
        create_label="Create Agent",
        update_label="Update Agent",
    ),
    "agents.update": GlobalOperationAdapter(
        operation_id="agents.update",
        resource_type="agent",
        create_model=None,
        update_model=AgentUpdate,
        get_path_template="/api/agents/{resource_id}",
        create_path="/api/agents",
        update_method="PUT",
        update_path_template="/api/agents/{resource_id}",
        delete_path_template="/api/agents/{resource_id}",
        reversible_update_fields=REVERSIBLE_AGENT_UPDATE_FIELDS,
        create_label="Create Agent",
        update_label="Update Agent",
    ),
    "forms.create": GlobalOperationAdapter(
        operation_id="forms.create",
        resource_type="form",
        create_model=FormCreate,
        update_model=None,
        get_path_template="/api/forms/{resource_id}",
        create_path="/api/forms",
        update_method="PATCH",
        update_path_template="/api/forms/{resource_id}",
        delete_path_template="/api/forms/{resource_id}?purge=true",
        reversible_update_fields=REVERSIBLE_FORM_UPDATE_FIELDS,
        create_label="Create Form",
        update_label="Update Form",
    ),
    "forms.update": GlobalOperationAdapter(
        operation_id="forms.update",
        resource_type="form",
        create_model=None,
        update_model=FormUpdate,
        get_path_template="/api/forms/{resource_id}",
        create_path="/api/forms",
        update_method="PATCH",
        update_path_template="/api/forms/{resource_id}",
        delete_path_template="/api/forms/{resource_id}",
        reversible_update_fields=REVERSIBLE_FORM_UPDATE_FIELDS,
        create_label="Create Form",
        update_label="Update Form",
    ),
    "tables.create": GlobalOperationAdapter(
        operation_id="tables.create",
        resource_type="table",
        create_model=TableCreate,
        update_model=None,
        get_path_template="/api/tables/{resource_id}",
        create_path="/api/tables",
        update_method="PATCH",
        update_path_template="/api/tables/{resource_id}",
        delete_path_template="/api/tables/{resource_id}",
        reversible_update_fields=REVERSIBLE_TABLE_UPDATE_FIELDS,
        create_label="Create Table",
        update_label="Update Table",
    ),
    "tables.update": GlobalOperationAdapter(
        operation_id="tables.update",
        resource_type="table",
        create_model=None,
        update_model=TableUpdate,
        get_path_template="/api/tables/{resource_id}",
        create_path="/api/tables",
        update_method="PATCH",
        update_path_template="/api/tables/{resource_id}",
        delete_path_template="/api/tables/{resource_id}",
        reversible_update_fields=REVERSIBLE_TABLE_UPDATE_FIELDS,
        create_label="Create Table",
        update_label="Update Table",
    ),
    "workflows.update": GlobalOperationAdapter(
        operation_id="workflows.update",
        resource_type="workflow",
        create_model=None,
        update_model=WorkflowUpdateRequest,
        get_path_template="/api/workflows/{resource_id}",
        create_path="/api/workflows/register",
        update_method="PATCH",
        update_path_template="/api/workflows/{resource_id}",
        delete_path_template="/api/workflows/{resource_id}",
        reversible_update_fields=REVERSIBLE_WORKFLOW_UPDATE_FIELDS,
        create_label="Register Workflow",
        update_label="Update Workflow",
    ),
    "apps.update": GlobalOperationAdapter(
        operation_id="apps.update",
        resource_type="application",
        create_model=None,
        update_model=ApplicationUpdate,
        get_path_template="/api/applications",
        create_path="/api/applications",
        update_method="PATCH",
        update_path_template="/api/applications/{resource_id}",
        delete_path_template="/api/applications/{resource_id}",
        reversible_update_fields=REVERSIBLE_APP_UPDATE_FIELDS,
        create_label="Create Application",
        update_label="Update Application",
    ),
}


def _adapter_for(operation_id: str) -> GlobalOperationAdapter:
    try:
        return _GLOBAL_OPERATION_ADAPTERS[operation_id]
    except KeyError as exc:
        raise GlobalOperationChangeError(
            f"{operation_id} is not implemented for Global operation changesets"
        ) from exc


async def _rest_json(
    context: "MCPContext",
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    from src.services.mcp_server.tools._http_bridge import call_rest

    return await call_rest(
        context,
        method,
        path,
        json_body=json_body,
        authorization_boundary="platform",
    )


async def _fetch_before_state(
    context: "MCPContext",
    *,
    operation_id: str,
    resource_id: str | None,
) -> dict[str, Any] | None:
    adapter = _adapter_for(operation_id)
    if adapter.is_create:
        return None
    if not resource_id:
        raise GlobalOperationChangeError("resource_id is required")
    return await adapter.fetch(context, resource_id)


def _validated_payload(
    *,
    operation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _adapter_for(operation_id).validate_payload(payload)


def _validate_reversible_before_state(
    *,
    operation_id: str,
    before_state: dict[str, Any] | None,
) -> None:
    if operation_id == "forms.update" and before_state and before_state.get("role_ids"):
        raise GlobalOperationChangeError(
            "forms.update cannot be safely staged for rollback when the existing "
            "Form has role_ids; workflow role side effects are not reversible yet"
        )


async def stage_global_operation_change(
    db: AsyncSession,
    *,
    solution_id: UUID,
    context: "MCPContext",
    operation_id: str,
    payload: dict[str, Any],
    resource_id: str | None = None,
    created_by: UUID | None = None,
) -> GlobalOperationChangeResult:
    """Validate and stage one Global operation without mutating live resources."""

    try:
        get_operation(operation_id)
    except KeyError as exc:
        raise GlobalOperationChangeError(f"Unknown operation_id: {operation_id}") from exc
    if operation_id in PLANNED_GLOBAL_OPERATION_IDS:
        raise GlobalOperationChangeError(
            f"{operation_id} is planned but fails closed until its adapter is implemented"
        )
    if operation_id not in SUPPORTED_GLOBAL_OPERATION_IDS:
        raise GlobalOperationChangeError(
            f"{operation_id} is not allowed in the Global Builder changeset"
        )
    try:
        validated_payload = _validated_payload(
            operation_id=operation_id,
            payload=payload,
        )
        before_state = await _fetch_before_state(
            context,
            operation_id=operation_id,
            resource_id=resource_id,
        )
        _validate_reversible_before_state(
            operation_id=operation_id,
            before_state=before_state,
        )
        validation_errors: list[str] = []
    except (ValidationError, GlobalOperationChangeError) as exc:
        validated_payload = dict(payload)
        before_state = None
        validation_errors = [str(exc)]
    row = SolutionGlobalOperationChange(
        solution_id=solution_id,
        operation_id=operation_id,
        resource_type=_operation_resource_type(operation_id),
        resource_id=resource_id,
        state="staged",
        payload=validated_payload,
        before_state=before_state,
        before_fingerprint=_fingerprint(before_state),
        validation_errors=validation_errors,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    await emit_audit(
        db,
        "builder.global_workspace.operation.stage",
        resource_type="solution",
        resource_id=solution_id,
        details={
            "change_id": str(row.id),
            "operation_id": operation_id,
            "staged_resource_id": resource_id,
            "valid": not validation_errors,
        },
    )
    await db.commit()
    return GlobalOperationChangeResult.from_row(row)


async def list_staged_global_operation_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> list[GlobalOperationChangeResult]:
    rows = (
        await db.scalars(
            select(SolutionGlobalOperationChange)
            .where(
                SolutionGlobalOperationChange.solution_id == solution_id,
                SolutionGlobalOperationChange.state.in_(
                    REVIEWABLE_GLOBAL_OPERATION_STATES
                ),
            )
            .order_by(SolutionGlobalOperationChange.created_at)
        )
    ).all()
    return [GlobalOperationChangeResult.from_row(row) for row in rows]


async def discard_staged_global_operation_change(
    db: AsyncSession,
    *,
    solution_id: UUID,
    change_id: UUID,
    requested_by: UUID | None,
) -> GlobalOperationChangeResult:
    row = await db.get(SolutionGlobalOperationChange, change_id)
    if (
        row is None
        or row.solution_id != solution_id
        or row.state not in REVIEWABLE_GLOBAL_OPERATION_STATES
    ):
        raise GlobalOperationChangeError("Staged Global operation change not found")
    row.state = "discarded"
    row.rolled_back_by = requested_by
    row.rolled_back_at = datetime.now(timezone.utc)
    await emit_audit(
        db,
        "builder.global_workspace.operation.discard",
        resource_type="solution",
        resource_id=solution_id,
        details={"operation_change_id": str(row.id)},
    )
    await db.commit()
    return GlobalOperationChangeResult.from_row(row)


async def validate_staged_global_operation_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> list[str]:
    rows = (
        await db.scalars(
            select(SolutionGlobalOperationChange).where(
                SolutionGlobalOperationChange.solution_id == solution_id,
                SolutionGlobalOperationChange.state.in_(
                    REVIEWABLE_GLOBAL_OPERATION_STATES
                ),
            )
        )
    ).all()
    errors: list[str] = []
    for row in rows:
        errors.extend(
            f"{row.operation_id} {row.id}: {error}"
            for error in (row.validation_errors or [])
        )
    return errors


async def list_applied_global_operation_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> list[GlobalOperationChangeResult]:
    latest_apply_job_id = await db.scalar(
        select(SolutionGlobalOperationChange.apply_job_id)
        .where(
            SolutionGlobalOperationChange.solution_id == solution_id,
            SolutionGlobalOperationChange.state == "applied",
            SolutionGlobalOperationChange.apply_job_id.is_not(None),
        )
        .order_by(SolutionGlobalOperationChange.applied_at.desc())
        .limit(1)
    )
    if latest_apply_job_id is None:
        return []
    rows = (
        await db.scalars(
            select(SolutionGlobalOperationChange)
            .where(
                SolutionGlobalOperationChange.solution_id == solution_id,
                SolutionGlobalOperationChange.state == "applied",
                SolutionGlobalOperationChange.apply_job_id == latest_apply_job_id,
            )
            .order_by(SolutionGlobalOperationChange.applied_at.desc())
        )
    ).all()
    return [GlobalOperationChangeResult.from_row(row) for row in rows]


async def apply_staged_global_operation_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
    context: "MCPContext",
    requested_by: UUID,
    apply_job_id: UUID | None = None,
    approved_changes: dict[UUID, str] | None = None,
) -> list[GlobalOperationChangeResult]:
    await recover_interrupted_global_operation_changes(db, solution_id=solution_id)
    query = select(SolutionGlobalOperationChange).where(
        SolutionGlobalOperationChange.solution_id == solution_id,
    )
    if approved_changes is not None:
        query = query.where(SolutionGlobalOperationChange.id.in_(approved_changes))
    if apply_job_id is None:
        query = query.where(SolutionGlobalOperationChange.state == "staged")
    else:
        query = query.where(
            (
                SolutionGlobalOperationChange.state == "staged"
            )
            | (
                (SolutionGlobalOperationChange.apply_job_id == apply_job_id)
                & (
                    SolutionGlobalOperationChange.state.in_(
                        ("applying", "applied", "failed")
                    )
                )
            )
        )
    rows = (
        await db.scalars(
            query.order_by(SolutionGlobalOperationChange.created_at)
        )
    ).all()
    existing_batch = [
        row
        for row in rows
        if apply_job_id is not None and row.apply_job_id == apply_job_id
    ]
    if existing_batch and all(row.state == "applied" for row in existing_batch):
        return [GlobalOperationChangeResult.from_row(row) for row in existing_batch]
    if approved_changes is not None:
        found_ids = {row.id for row in rows}
        missing_ids = set(approved_changes) - found_ids
        if missing_ids:
            raise GlobalOperationConflict(
                "Approved Global operation changes are missing: "
                + ", ".join(str(item) for item in sorted(missing_ids, key=str))
            )
        for row in rows:
            if operation_change_review_fingerprint(row) != approved_changes[row.id]:
                raise GlobalOperationConflict(
                    f"Approved Global operation change {row.id} changed after review"
                )
    applied_rows: list[SolutionGlobalOperationChange] = []
    try:
        for row in rows:
            if row.state == "applied" and row.apply_job_id == apply_job_id:
                applied_rows.append(row)
                continue
            if row.state != "staged":
                raise GlobalOperationChangeError(
                    f"Operation {row.id} is {row.state}; discard or repair it before apply"
                )
            raw_errors = row.validation_errors
            validation_errors = (
                [str(error) for error in raw_errors]
                if isinstance(raw_errors, list)
                else []
            )
            if validation_errors:
                raise GlobalOperationChangeError(
                    f"Cannot apply invalid staged operation {row.id}"
                )
            payload = _validated_payload(
                operation_id=row.operation_id,
                payload=row.payload,
            )
            row.apply_job_id = apply_job_id
            row.state = "applying"
            await db.commit()
            current = await _fetch_before_state(
                context,
                operation_id=row.operation_id,
                resource_id=row.resource_id,
            )
            _validate_reversible_before_state(
                operation_id=row.operation_id,
                before_state=current,
            )
            if _fingerprint(current) != row.before_fingerprint:
                row.state = "staged"
                await db.commit()
                raise GlobalOperationConflict(
                    f"Live {row.resource_type} changed before operation {row.id} applied"
                )

            resource_id, body = await _adapter_for(row.operation_id).apply(
                context,
                resource_id=row.resource_id,
                payload=payload,
                change_id=row.id,
            )
            row.resource_id = resource_id
            row.applied_state = body
            row.applied_fingerprint = _fingerprint(row.applied_state)
            row.state = "applied"
            row.applied_by = requested_by
            row.applied_at = datetime.now(timezone.utc)
            applied_rows.append(row)
            await db.commit()
    except Exception:
        if applied_rows:
            await _compensate_applied_rows(
                db,
                solution_id=solution_id,
                context=context,
                rows=list(reversed(applied_rows)),
                requested_by=requested_by,
            )
        raise
    results = [GlobalOperationChangeResult.from_row(row) for row in applied_rows]
    if results:
        await emit_audit(
            db,
            "builder.global_workspace.operation.apply",
            resource_type="solution",
            resource_id=solution_id,
            details={"operation_change_ids": [str(item.id) for item in results]},
        )
        await db.commit()
    return results


async def _compensate_applied_rows(
    db: AsyncSession,
    *,
    solution_id: UUID,
    context: "MCPContext",
    rows: list[SolutionGlobalOperationChange],
    requested_by: UUID,
) -> None:
    uncompensated: list[str] = []
    for row in rows:
        try:
            if not row.resource_id:
                uncompensated.append(str(row.id))
                continue
            current = await _adapter_for(row.operation_id).fetch(context, row.resource_id)
            if _fingerprint(current) != row.applied_fingerprint:
                uncompensated.append(str(row.id))
                continue
            await _assert_create_rollback_has_no_dependents(db, row)
            await _adapter_for(row.operation_id).rollback(
                context,
                resource_id=row.resource_id,
                before_state=row.before_state,
                change_id=row.id,
            )
            row.state = "rolled_back"
            row.rolled_back_by = requested_by
            row.rolled_back_at = datetime.now(timezone.utc)
        except GlobalOperationChangeError:
            uncompensated.append(str(row.id))
    await emit_audit(
        db,
        "builder.global_workspace.operation.compensate",
        resource_type="solution",
        resource_id=solution_id,
        details={"operation_change_ids": [str(row.id) for row in rows]},
    )
    await db.commit()
    if uncompensated:
        raise GlobalOperationConflict(
            "Applied operation changes could not be fully compensated: "
            + ", ".join(uncompensated)
        )


async def _count_rows(db: AsyncSession, model, criterion) -> int:
    return int(
        await db.scalar(select(func.count()).select_from(model).where(criterion)) or 0
    )


async def _assert_create_rollback_has_no_dependents(
    db: AsyncSession,
    row: SolutionGlobalOperationChange,
) -> None:
    if not row.operation_id.endswith(".create") or not row.resource_id:
        return
    resource_uuid = UUID(str(row.resource_id))
    dependent_counts: dict[str, int] = {}
    if row.operation_id == "tables.create":
        dependent_counts["documents"] = await _count_rows(
            db,
            Document,
            Document.table_id == resource_uuid,
        )
    elif row.operation_id == "forms.create":
        dependent_counts["executions"] = await _count_rows(
            db,
            Execution,
            Execution.form_id == resource_uuid,
        )
        dependent_counts["form_publications"] = await _count_rows(
            db,
            FormPublication,
            FormPublication.form_id == resource_uuid,
        )
        dependent_counts["form_embed_secrets"] = await _count_rows(
            db,
            FormEmbedSecret,
            FormEmbedSecret.form_id == resource_uuid,
        )
    elif row.operation_id == "agents.create":
        dependent_counts["agent_runs"] = await _count_rows(
            db,
            AgentRun,
            AgentRun.agent_id == resource_uuid,
        )
        dependent_counts["event_subscriptions"] = await _count_rows(
            db,
            EventSubscription,
            EventSubscription.agent_id == resource_uuid,
        )
        dependent_counts["conversations"] = await _count_rows(
            db,
            Conversation,
            Conversation.agent_id == resource_uuid,
        )
        dependent_counts["inbound_delegations"] = await _count_rows(
            db,
            AgentDelegation,
            AgentDelegation.child_agent_id == resource_uuid,
        )
        dependent_counts["prompt_history"] = await _count_rows(
            db,
            AgentPromptHistory,
            AgentPromptHistory.agent_id == resource_uuid,
        )
    blockers = {name: count for name, count in dependent_counts.items() if count > 0}
    if blockers:
        details = ", ".join(
            f"{name}={count}" for name, count in sorted(blockers.items())
        )
        raise GlobalOperationConflict(
            f"Cannot roll back {row.operation_id} {row.id}; "
            f"dependent runtime data exists: {details}"
        )


async def recover_interrupted_global_operation_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> int:
    rows = (
        await db.scalars(
            select(SolutionGlobalOperationChange).where(
                SolutionGlobalOperationChange.solution_id == solution_id,
                SolutionGlobalOperationChange.state == "applying",
            )
        )
    ).all()
    for row in rows:
        if row.applied_fingerprint:
            row.state = "applied"
        else:
            row.state = "failed"
            errors = list(row.validation_errors or [])
            errors.append(
                "Apply was interrupted after the operation started. Bifrost cannot "
                "prove whether the canonical REST mutation committed; review the "
                "live resource and discard or repair this change manually."
            )
            row.validation_errors = errors
    if rows:
        await emit_audit(
            db,
            "builder.global_workspace.operation.recover",
            resource_type="solution",
            resource_id=solution_id,
            details={"operation_change_ids": [str(row.id) for row in rows]},
        )
        await db.commit()
    return len(rows)


async def rollback_applied_global_operation_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
    context: "MCPContext",
    requested_by: UUID,
    rollback_job_id: UUID | None = None,
    approved_changes: dict[UUID, str] | None = None,
) -> list[GlobalOperationChangeResult]:
    query = select(SolutionGlobalOperationChange).where(
        SolutionGlobalOperationChange.solution_id == solution_id,
    )
    if approved_changes is not None:
        query = query.where(SolutionGlobalOperationChange.id.in_(approved_changes))
    if rollback_job_id is None:
        query = query.where(SolutionGlobalOperationChange.state == "applied")
    else:
        query = query.where(
            (
                SolutionGlobalOperationChange.state == "applied"
            )
            | (
                (SolutionGlobalOperationChange.rollback_job_id == rollback_job_id)
                & (
                    SolutionGlobalOperationChange.state.in_(
                        ("rolled_back", "failed")
                    )
                )
            )
        )
    rows = (
        await db.scalars(
            query.order_by(SolutionGlobalOperationChange.applied_at.desc())
        )
    ).all()
    existing_batch = [
        row
        for row in rows
        if rollback_job_id is not None and row.rollback_job_id == rollback_job_id
    ]
    if (
        existing_batch
        and len(existing_batch) == len(rows)
        and all(row.state == "rolled_back" for row in existing_batch)
    ):
        return [GlobalOperationChangeResult.from_row(row) for row in existing_batch]
    if approved_changes is not None:
        found_ids = {row.id for row in rows}
        missing_ids = set(approved_changes) - found_ids
        if missing_ids:
            raise GlobalOperationConflict(
                "Approved applied Global operation changes are missing: "
                + ", ".join(str(item) for item in sorted(missing_ids, key=str))
            )
        for row in rows:
            if row.state == "rolled_back" and row.rollback_job_id == rollback_job_id:
                continue
            if operation_change_applied_fingerprint(row) != approved_changes[row.id]:
                raise GlobalOperationConflict(
                    f"Applied Global operation change {row.id} changed after review"
                )
    for row in rows:
        if row.state == "rolled_back" and row.rollback_job_id == rollback_job_id:
            continue
        if row.state != "applied":
            raise GlobalOperationChangeError(
                f"Operation {row.id} is {row.state}; it cannot be rolled back"
            )
        if not row.resource_id:
            raise GlobalOperationChangeError(
                f"{row.operation_id} is missing rollback resource state"
            )
        current = await _adapter_for(row.operation_id).fetch(context, row.resource_id)
        if _fingerprint(current) != row.applied_fingerprint:
            raise GlobalOperationConflict(
                f"Live {row.resource_type} changed after operation {row.id} applied"
            )
        await _assert_create_rollback_has_no_dependents(db, row)
    rolled_back: list[GlobalOperationChangeResult] = []
    for row in rows:
        if row.state == "rolled_back" and row.rollback_job_id == rollback_job_id:
            rolled_back.append(GlobalOperationChangeResult.from_row(row))
            continue
        await _adapter_for(row.operation_id).rollback(
            context,
            resource_id=row.resource_id,
            before_state=row.before_state,
            change_id=row.id,
        )
        row.state = "rolled_back"
        row.rollback_job_id = rollback_job_id
        row.rolled_back_by = requested_by
        row.rolled_back_at = datetime.now(timezone.utc)
        rolled_back.append(GlobalOperationChangeResult.from_row(row))
        await db.commit()
    if rolled_back:
        await emit_audit(
            db,
            "builder.global_workspace.operation.rollback",
            resource_type="solution",
            resource_id=solution_id,
            details={"operation_change_ids": [str(item.id) for item in rolled_back]},
        )
        await db.commit()
    return rolled_back


__all__ = [
    "GlobalOperationChangeError",
    "GlobalOperationConflict",
    "apply_staged_global_operation_changes",
    "discard_staged_global_operation_change",
    "global_operation_inventory",
    "list_applied_global_operation_changes",
    "list_staged_global_operation_changes",
    "operation_change_applied_fingerprint",
    "operation_change_review_fingerprint",
    "recover_interrupted_global_operation_changes",
    "rollback_applied_global_operation_changes",
    "stage_global_operation_change",
    "validate_staged_global_operation_changes",
]
