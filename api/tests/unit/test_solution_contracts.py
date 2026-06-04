"""Contract-level tests for Solution DTOs.

scope is DERIVED from organization_id (NULL == global), not stored on the ORM —
so a global install must serialize scope='global', not the field default
(Codex P2 fix).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.models.contracts.solutions import Solution as SolutionDTO


def _row(org_id):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug="s",
        name="S",
        organization_id=org_id,
        global_repo_access=False,
        git_connected=False,
        git_repo_url=None,
    )


def test_global_install_serializes_scope_global() -> None:
    dto = SolutionDTO.model_validate(_row(None))
    assert dto.scope == "global"
    assert dto.model_dump()["scope"] == "global"


def test_org_install_serializes_scope_org() -> None:
    dto = SolutionDTO.model_validate(_row(uuid.uuid4()))
    assert dto.scope == "org"
    assert dto.model_dump()["scope"] == "org"
