"""Business logic for reviewing a form's anonymous public capability."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shared.form_runtime import form_capability_fingerprint
from src.repositories.workflows import WorkflowRepository
from src.services.solution_scope import solution_allows_global


async def _resolve_form_workflow(db: AsyncSession, form: Any, ref: str | None):
    if not ref:
        return None

    repository = WorkflowRepository(
        db,
        org_id=form.organization_id,
        is_superuser=True,
    )
    allow_shared = form.solution_id is None or await solution_allows_global(
        db, form.solution_id
    )
    return await repository.resolve(
        ref,
        solution_scope=form.solution_id,
        allow_shared_fallback=allow_shared,
    )


async def build_publication_review(db: AsyncSession, form: Any) -> dict[str, Any]:
    """Resolve and describe exactly what publishing this form exposes."""

    blockers: list[dict[str, str]] = []
    warnings: list[str] = []

    submission = await _resolve_form_workflow(db, form, form.workflow_id)
    if submission is None or not submission.is_active:
        blockers.append(
            {
                "code": "submission_workflow_unavailable",
                "message": "The submission workflow is missing or inactive.",
            }
        )
    elif submission.type not in {"workflow", "tool"}:
        blockers.append(
            {
                "code": "submission_workflow_invalid_type",
                "message": "The submission binding is not an executable workflow.",
            }
        )

    startup = await _resolve_form_workflow(db, form, form.launch_workflow_id)
    if form.launch_workflow_id and (startup is None or not startup.is_active):
        blockers.append(
            {
                "code": "startup_workflow_unavailable",
                "message": "The startup workflow is missing or inactive.",
            }
        )
    elif startup is not None:
        warnings.append("Startup workflow output is visible to anonymous form visitors.")

    provider_fields: list[dict[str, Any]] = []
    file_fields: list[str] = []
    for field in sorted(form.fields, key=lambda item: item.position):
        if field.type == "html":
            blockers.append(
                {
                    "code": "public_html_field",
                    "field_name": field.name,
                    "message": "HTML/JSX display fields cannot be published publicly.",
                }
            )

        if field.type == "file":
            file_fields.append(field.name)

        if not field.data_provider_id:
            continue

        provider = await _resolve_form_workflow(db, form, str(field.data_provider_id))
        if provider is None or not provider.is_active or provider.type != "data_provider":
            blockers.append(
                {
                    "code": "data_provider_unavailable",
                    "field_name": field.name,
                    "message": "The field data provider is missing, inactive, or invalid.",
                }
            )
            continue

        provider_fields.append(
            {
                "field_name": field.name,
                "provider_ref": str(provider.id),
                "provider_name": provider.name,
                "configured_inputs": sorted((field.data_provider_inputs or {}).keys()),
                "metadata_paths": sorted(set((field.auto_fill or {}).values())),
            }
        )

    return {
        "fingerprint": form_capability_fingerprint(form),
        "submission_workflow": (
            {
                "ref": str(submission.id),
                "name": submission.name,
            }
            if submission is not None
            else None
        ),
        "startup_workflow": (
            {"ref": str(startup.id), "name": startup.name}
            if startup is not None
            else None
        ),
        "provider_fields": provider_fields,
        "file_fields": file_fields,
        "warnings": warnings,
        "blockers": blockers,
    }
