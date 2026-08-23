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


def test_missing_legacy_assignments_use_primary_profile():
    migration = _load_migration()
    executed: list[tuple[str, dict]] = []

    class Bind:
        def execute(self, statement, params=None):
            executed.append((" ".join(str(statement).split()), params or {}))
            return _Result()

    profile_id = uuid4()
    now = migration.datetime.now(migration.timezone.utc)
    for assignment_key in migration.ASSIGNMENT_KEYS - {"primary", "chat_default"}:
        migration._insert_assignment_if_missing(Bind(), assignment_key, profile_id, now)

    assert {params["assignment_key"] for _, params in executed} == {
        "summarization",
        "tuning",
        "image_generation",
        "video_generation",
    }
    assert all("ON CONFLICT (assignment_key) DO NOTHING" in sql for sql, _ in executed)
    assert {params["profile_id"] for _, params in executed} == {profile_id}


def test_legacy_provider_config_preserves_profiles_and_assignments(monkeypatch):
    migration = _load_migration()
    config = {
        "provider": "openai",
        "endpoint": "https://openrouter.ai/api/v1/",
        "encrypted_api_key": "encrypted-key",
        "model": "openai/gpt-5",
        "max_tokens": 32000,
        "chat_fast_label": "Quick",
        "chat_fast_model": "openai/gpt-5-mini",
        "chat_fast_capabilities": {"reasoning": False},
        "chat_balanced_label": "Everyday",
        "chat_balanced_model": "anthropic/claude-sonnet-4",
        "chat_balanced_capabilities": {"reasoning": True},
        "chat_pro_label": "Deep",
        "chat_pro_model": "openai/o3",
        "chat_pro_capabilities": {"reasoning": True},
        "summarization_model": "openai/gpt-5-nano",
        "tuning_model": "openai/gpt-5-mini",
        "image_generation_model": "openai/gpt-image-1",
        "video_generation_model": "openai/sora-2",
    }

    class Bind:
        def execute(self, statement, params=None):
            sql = " ".join(str(statement).split())
            assert "FROM system_configs" in sql
            return _Result(first={"value_json": config})

    connection_id = uuid4()
    profiles: list[dict] = []
    assignments: dict[str, UUID] = {}
    default_assignments: dict[str, UUID] = {}

    def create_connection(bind, provider, endpoint, encrypted_key, now):
        assert isinstance(bind, Bind)
        assert provider == "openrouter"
        assert endpoint == migration.OPENROUTER_DEFAULT_ENDPOINT
        assert encrypted_key == "encrypted-key"
        return connection_id

    def create_profile(bind, **values):
        assert isinstance(bind, Bind)
        assert values["connection_id"] == connection_id
        profile_id = uuid4()
        profiles.append({"id": profile_id, **values})
        return profile_id

    monkeypatch.setattr(migration.op, "get_bind", Bind)
    monkeypatch.setattr(migration, "_get_or_create_connection", create_connection)
    monkeypatch.setattr(migration, "_get_or_create_profile", create_profile)
    monkeypatch.setattr(
        migration,
        "_find_matching_profile",
        lambda _bind, _connection_id, model: next(
            (profile["id"] for profile in profiles if profile["model"] == model),
            None,
        ),
    )
    monkeypatch.setattr(
        migration,
        "_upsert_assignment",
        lambda _bind, key, profile_id, _now: assignments.__setitem__(key, profile_id),
    )
    monkeypatch.setattr(
        migration,
        "_insert_assignment_if_missing",
        lambda _bind, key, profile_id, _now: default_assignments.__setitem__(
            key, profile_id
        ),
    )

    migration._migrate_legacy_llm_config()

    by_name = {profile["name"]: profile for profile in profiles}
    assert set(by_name) == {
        "Default",
        "Quick",
        "Everyday",
        "Deep",
        "Summarization",
        "Image Generation",
        "Video Generation",
    }
    assert by_name["Default"]["model"] == "openai/gpt-5"
    assert by_name["Default"]["enabled_for_chat"] is False
    assert by_name["Quick"]["model"] == "openai/gpt-5-mini"
    assert by_name["Quick"]["enabled_for_chat"] is True
    assert by_name["Quick"]["capabilities"] == {"reasoning": False}
    assert by_name["Everyday"]["model"] == "anthropic/claude-sonnet-4"
    assert by_name["Everyday"]["enabled_for_chat"] is True
    assert by_name["Everyday"]["capabilities"] == {"reasoning": True}
    assert by_name["Deep"]["model"] == "openai/o3"
    assert by_name["Deep"]["enabled_for_chat"] is True
    assert assignments == {
        "primary": by_name["Default"]["id"],
        "chat_default": by_name["Everyday"]["id"],
        "summarization": by_name["Summarization"]["id"],
        "tuning": by_name["Quick"]["id"],
        "image_generation": by_name["Image Generation"]["id"],
        "video_generation": by_name["Video Generation"]["id"],
    }
    assert default_assignments == {
        "summarization": by_name["Default"]["id"],
        "tuning": by_name["Default"]["id"],
        "image_generation": by_name["Default"]["id"],
        "video_generation": by_name["Default"]["id"],
    }
