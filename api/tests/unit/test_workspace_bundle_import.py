"""Partial workspace imports select explicitly and never run stale deletion."""
from __future__ import annotations

from uuid import UUID

import pytest


def test_partial_selection_rewrites_kept_conflict_references() -> None:
    from bifrost.manifest import Manifest, ManifestForm, ManifestWorkflow
    from src.services.manifest_import import PartialImportSelection, rewrite_manifest_references

    source_workflow = UUID("11111111-1111-1111-1111-111111111111")
    target_workflow = UUID("22222222-2222-2222-2222-222222222222")
    form = ManifestForm(id="33333333-3333-3333-3333-333333333333", name="Intake", workflow_id=str(source_workflow))
    rewritten = rewrite_manifest_references(
        Manifest(
            workflows={str(source_workflow): ManifestWorkflow(
                id=str(source_workflow), name="run", path="workflows/run.py", function_name="run",
            )},
            forms={form.id: form},
        ),
        PartialImportSelection(
            included_source_ids=frozenset({form.id}),
            target_ids={source_workflow: target_workflow},
        ),
    )

    assert rewritten.forms[form.id].workflow_id == str(target_workflow)
    assert rewritten.workflows[str(target_workflow)].organization_id is None


def test_partial_selection_keeps_only_explicit_source_ids() -> None:
    from bifrost.manifest import Manifest, ManifestWorkflow
    from src.services.manifest_import import PartialImportSelection, filter_partial_manifest

    first = ManifestWorkflow(id="11111111-1111-1111-1111-111111111111", name="one", path="workflows/one.py", function_name="one")
    second = ManifestWorkflow(id="22222222-2222-2222-2222-222222222222", name="two", path="workflows/two.py", function_name="two")
    selected = filter_partial_manifest(
        Manifest(workflows={first.id: first, second.id: second}),
        PartialImportSelection(included_source_ids=frozenset({first.id}), target_ids={}),
    )

    assert list(selected.workflows) == [first.id]


@pytest.mark.asyncio
async def test_importer_requires_one_decision_for_every_conflict(tmp_path) -> None:
    from bifrost.manifest import Manifest
    from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview
    from src.services.solutions.workspace_bundle_import import (
        WorkspaceBundleDecisionError,
        WorkspaceBundleImporter,
    )
    from src.services.solutions.workspace_bundle_plan import PlannedWorkspaceBundle

    plan = PlannedWorkspaceBundle(
        preview=WorkspaceBundlePreview(
            preview_token="p", package_name="P", package_sha256="",
            items=[WorkspaceBundleItem(id="entity:app:x", kind="app", name="x", classification="conflict")],
        ), manifest=Manifest(), id_map={}, work_dir=tmp_path,
    )

    with pytest.raises(WorkspaceBundleDecisionError, match="every conflict"):
        await WorkspaceBundleImporter(object()).apply(plan, [])
