"""ORM-level tests for Solutions: the `Solution` install entity and the
nullable `solution_id` FK that marks an entity as solution-managed.

Criteria 1,2,8,9,10,14,16 (Solutions success criteria) all rest on this column
existing on every portable entity (workflows, apps, forms, agents, tables).
"""
from __future__ import annotations

import pytest

from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow

# (ORM class, table name) for every entity that gains solution_id.
_ENTITIES = [
    (Workflow, "workflows"),
    (Application, "applications"),
    (Form, "forms"),
    (Agent, "agents"),
    (Table, "tables"),
]


@pytest.mark.parametrize("orm,table", _ENTITIES, ids=[t for _, t in _ENTITIES])
def test_entity_has_nullable_solution_id(orm: type, table: str) -> None:
    cols = orm.__table__.columns
    assert "solution_id" in cols, f"{table} is missing solution_id"
    assert cols["solution_id"].nullable is True, f"{table}.solution_id must be nullable"


def test_solution_orm_shape() -> None:
    """The Solution install entity exists with the locked descriptor fields."""
    from src.models.orm.solutions import Solution

    cols = Solution.__table__.columns
    for field in (
        "id",
        "slug",
        "name",
        "organization_id",  # None == global scope
        "global_repo_access",
        "git_connected",
        "git_repo_url",
        "created_at",
        "updated_at",
    ):
        assert field in cols, f"Solution is missing {field}"
    # organization_id is nullable (NULL == global scope per locked decision 3.3).
    assert cols["organization_id"].nullable is True


def test_solution_export_job_orm_shape() -> None:
    """Durable backup export jobs have the scheduler-owned state columns."""
    from src.models.orm.solution_export_jobs import SolutionExportJob

    cols = SolutionExportJob.__table__.columns
    for field in (
        "id",
        "solution_id",
        "organization_id",
        "requested_by_id",
        "notification_id",
        "status",
        "progress_percent",
        "message",
        "failure_message",
        "encrypted_options",
        "artifact_storage_key",
        "artifact_filename",
        "artifact_size_bytes",
        "artifact_sha256",
        "expires_at",
        "completed_at",
        "claimed_at",
        "created_at",
        "updated_at",
    ):
        assert field in cols, f"SolutionExportJob is missing {field}"

    assert cols["solution_id"].nullable is False
    # NULL organization_id mirrors Solution global scope.
    assert cols["organization_id"].nullable is True
    assert cols["requested_by_id"].nullable is True
    assert cols["notification_id"].nullable is True
    assert cols["status"].default.arg == "pending"
    assert cols["progress_percent"].default.arg == 0
