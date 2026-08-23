"""Behavioral coverage for legacy agent-model profile migration."""

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260822_ai_model_profiles.py"
)


class _Result:
    def __init__(self, *, rows=None, first=None, scalar=None):
        self._rows = rows or []
        self._first = first
        self._scalar = scalar

    def all(self):
        return self._rows

    def mappings(self):
        return self

    def first(self):
        return self._first

    def scalar(self):
        return self._scalar


class _MigrationBind:
    def __init__(self):
        self.existing_profile_id = uuid4()
        self.inserted_profiles: list[dict] = []
        self.agent_updates: list[dict] = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        if "SELECT DISTINCT btrim(llm_model)" in sql:
            return _Result(rows=[("claude-sonnet",), ("gpt-5",)])
        if "SELECT profile.connection_id" in sql:
            return _Result(first={"connection_id": uuid4()})
        if "SELECT id FROM ai_model_profiles" in sql:
            profile_id = self.existing_profile_id if params["model"] == "gpt-5" else None
            return _Result(scalar=profile_id)
        if "SELECT 1 FROM ai_model_profiles" in sql:
            return _Result(scalar=None)
        if "INSERT INTO ai_model_profiles" in sql:
            assert "max_tokens" not in sql
            self.inserted_profiles.append(params)
            return _Result()
        if "UPDATE agents SET llm_profile_id" in sql:
            self.agent_updates.append(params)
            return _Result()
        raise AssertionError(f"Unexpected migration SQL: {sql}")


def _load_migration():
    spec = importlib.util.spec_from_file_location("_ai_model_profiles_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_distinct_agent_model_reuses_or_creates_one_profile(monkeypatch):
    migration = _load_migration()
    bind = _MigrationBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration._migrate_agent_profiles()

    assert [profile["model"] for profile in bind.inserted_profiles] == ["claude-sonnet"]
    updates = {update["model"]: update["profile_id"] for update in bind.agent_updates}
    assert set(updates) == {"claude-sonnet", "gpt-5"}
    assert updates["gpt-5"] == bind.existing_profile_id
    assert isinstance(updates["claude-sonnet"], UUID)
