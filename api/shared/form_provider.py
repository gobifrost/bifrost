"""Server-owned execution and projection for form field data providers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from shared.form_execution import create_form_execution_context
from shared.run_lineage import RunLineage


class FormProviderError(ValueError):
    pass


def _provider_inputs(field: Any, browser_inputs: dict[str, Any]) -> dict[str, Any]:
    configured = field.data_provider_inputs or {}
    unknown = sorted(set(browser_inputs) - set(configured))
    if unknown:
        raise FormProviderError(f"Unknown provider input: {unknown[0]}")

    resolved: dict[str, Any] = {}
    for name, config in configured.items():
        mode = config.get("mode")
        if mode == "static":
            resolved[name] = config.get("value")
        elif name in browser_inputs:
            resolved[name] = browser_inputs[name]
    return resolved


def _normalize_options(result: Any, metadata_keys: set[str]) -> list[dict[str, Any]]:
    if not isinstance(result, list):
        raise FormProviderError("Provider returned an invalid option list")

    options: list[dict[str, Any]] = []
    for raw in result[:500]:
        if not isinstance(raw, dict) or "value" not in raw:
            continue
        value = str(raw["value"])
        option: dict[str, Any] = {
            "value": value,
            "label": str(raw.get("label", value)),
            "description": (
                str(raw["description"])
                if raw.get("description") is not None
                else None
            ),
        }
        raw_metadata = raw.get("metadata")
        if metadata_keys and isinstance(raw_metadata, dict):
            projected = {
                key: raw_metadata[key] for key in metadata_keys if key in raw_metadata
            }
            option["metadata"] = projected or None
        else:
            option["metadata"] = None
        options.append(option)
    return options


async def execute_form_field_provider(
    *,
    db: Any,
    form: Any,
    field: Any,
    user: Any,
    caller_org_id: Any,
    browser_inputs: dict[str, Any],
    lineage: RunLineage,
) -> list[dict[str, Any]]:
    """Execute only the provider persisted on ``field`` in the form's scope."""

    from src.repositories.workflows import WorkflowRepository
    from src.services.execution.service import run_workflow
    from src.services.solution_scope import solution_allows_global
    from src.services.authorization.enforce import load_caller, user_reach

    anchor_org_id = form.organization_id or caller_org_id
    repo = WorkflowRepository(db, org_id=anchor_org_id, is_superuser=True)
    allow_shared = (
        form.solution_id is None
        or await solution_allows_global(db, form.solution_id)
    )
    provider = await repo.resolve(
        str(field.data_provider_id),
        solution_scope=form.solution_id,
        allow_shared_fallback=allow_shared,
    )
    if provider is None or not provider.is_active or provider.type != "data_provider":
        raise FormProviderError("Provider unavailable")
    # The form capability is the resource gate; authenticated callers still
    # cannot use it to resolve a workflow beyond their original reach.
    if not user.embed:
        caller = await load_caller(db, user)
        if not user_reach(caller).covers(provider.organization_id):
            raise FormProviderError("Provider unavailable")

    context = await create_form_execution_context(
        db,
        user=user,
        lineage=lineage,
        execution_id=str(uuid4()),
        organization_id=anchor_org_id,
        is_platform_admin=user.is_superuser,
    )
    response = await run_workflow(
        context=context,
        workflow_id=str(provider.id),
        input_data=_provider_inputs(field, browser_inputs),
        form_id=str(form.id),
        transient=True,
        sync=True,
        lineage=lineage,
    )
    metadata_keys = set((field.auto_fill or {}).values())
    return _normalize_options(response.result, metadata_keys)
