from __future__ import annotations

from pathlib import Path


ORM_DIR = Path(__file__).parents[3] / "src" / "models" / "orm"

CODEQL_UNSAFE_CYCLIC_IMPORT_MODULES = [
    "agent_runs.py",
    "agents.py",
    "ai_usage.py",
    "applications.py",
    "cli.py",
    "config.py",
    "developer.py",
    "executions.py",
    "forms.py",
    "integrations.py",
    "knowledge.py",
    "mfa.py",
    "oauth.py",
    "organizations.py",
    "tables.py",
    "users.py",
    "workflow_roles.py",
    "workflows.py",
]


def test_codeql_cyclic_import_cluster_uses_string_forward_refs() -> None:
    """Alerted ORM peers should stay as string refs, without static imports."""

    for filename in CODEQL_UNSAFE_CYCLIC_IMPORT_MODULES:
        source = (ORM_DIR / filename).read_text(encoding="utf-8")

        assert "TYPE_CHECKING" not in source
        assert "if TYPE_CHECKING:" not in source


def test_orm_package_exports_still_import() -> None:
    from src.models.orm import AgentRun, AIUsage, Organization, User, Workflow

    assert AgentRun.__tablename__ == "agent_runs"
    assert AIUsage.__tablename__ == "ai_usage"
    assert Organization.__tablename__ == "organizations"
    assert User.__tablename__ == "users"
    assert Workflow.__tablename__ == "workflows"
