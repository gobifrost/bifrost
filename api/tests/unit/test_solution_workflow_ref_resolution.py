"""Workflow identifiers resolve only inside the walker's authorized universe."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from src.services.solutions.dependency_walker import SolutionDependencyWalker


@pytest.mark.parametrize("reference", [
    "64b39c66-d8de-4f4d-b838-a1915dfd9b5c", "workflows/child.py::run", "child",
])
def test_supported_workflow_identifiers(reference):
    workflow = SimpleNamespace(id=UUID("64b39c66-d8de-4f4d-b838-a1915dfd9b5c"), name="child")
    assert SolutionDependencyWalker._resolve_workflow_ref(
        reference, {workflow.id: workflow}, {"workflows/child.py::run": workflow}
    ) is workflow


def test_uuid_outside_authorized_universe_is_not_resolved():
    assert SolutionDependencyWalker._resolve_workflow_ref(
        "64b39c66-d8de-4f4d-b838-a1915dfd9b5c", {}, {}
    ) is None
