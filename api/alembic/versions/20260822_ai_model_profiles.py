"""add reusable AI provider connections and model profiles

Revision ID: 20260822_ai_model_profiles
Revises: 20260816_artifact_workspace
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260822_ai_model_profiles"
down_revision = "20260816_artifact_workspace"
branch_labels = None
depends_on = None

ASSIGNMENT_KEYS = {
    "primary",
    "summarization",
    "tuning",
    "image_generation",
    "video_generation",
    "chat_default",
}
OPENROUTER_DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1"


def upgrade() -> None:
    op.create_table(
        "ai_provider_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "provider IN ('openai', 'anthropic', 'google', 'openrouter', 'openai_compatible')",
            name="ck_ai_provider_connections_provider",
        ),
    )
    op.create_index(
        "uq_ai_provider_connections_name_ci",
        "ai_provider_connections",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "ai_model_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_provider_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled_for_chat", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("uq_ai_model_profiles_name_ci", "ai_model_profiles", [sa.text("lower(name)")], unique=True)
    op.create_index("ix_ai_model_profiles_connection_id", "ai_model_profiles", ["connection_id"])
    op.create_index("ix_ai_model_profiles_enabled_for_chat", "ai_model_profiles", ["enabled_for_chat"])
    op.add_column(
        "agents",
        sa.Column("llm_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agents_llm_profile_id_ai_model_profiles",
        "agents",
        "ai_model_profiles",
        ["llm_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_agents_llm_profile_id", "agents", ["llm_profile_id"])
    op.create_table(
        "ai_model_assignments",
        sa.Column("assignment_key", sa.String(length=50), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_model_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "assignment_key IN ('primary', 'summarization', 'tuning', 'image_generation', 'video_generation', 'chat_default')",
            name="ck_ai_model_assignments_key",
        ),
    )
    op.create_index("ix_ai_model_assignments_profile_id", "ai_model_assignments", ["profile_id"])
    op.create_table(
        "ai_embedding_configs",
        sa.Column("key", sa.String(length=50), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_provider_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False, server_default="1536"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("key = 'default'", name="ck_ai_embedding_configs_singleton"),
        sa.CheckConstraint("dimensions > 0", name="ck_ai_embedding_configs_dimensions_positive"),
    )
    op.create_index("ix_ai_embedding_configs_connection_id", "ai_embedding_configs", ["connection_id"])
    _migrate_legacy_llm_config()
    _migrate_ai_behavior()
    _migrate_embedding_config()
    _migrate_agent_profiles()
    op.drop_column("agents", "llm_model")


def downgrade() -> None:
    _restore_legacy_ai_behavior()
    op.add_column("agents", sa.Column("llm_model", sa.String(length=100), nullable=True))
    op.execute(
        """
        UPDATE agents AS agent
        SET llm_model = profile.model
        FROM ai_model_profiles AS profile
        WHERE agent.llm_profile_id = profile.id
        """
    )
    op.drop_index("ix_agents_llm_profile_id", table_name="agents")
    op.drop_constraint(
        "fk_agents_llm_profile_id_ai_model_profiles",
        "agents",
        type_="foreignkey",
    )
    op.drop_column("agents", "llm_profile_id")
    op.drop_index("ix_ai_model_assignments_profile_id", table_name="ai_model_assignments")
    op.drop_table("ai_model_assignments")
    op.drop_index("ix_ai_embedding_configs_connection_id", table_name="ai_embedding_configs")
    op.drop_table("ai_embedding_configs")
    op.drop_index("ix_ai_model_profiles_enabled_for_chat", table_name="ai_model_profiles")
    op.drop_index("ix_ai_model_profiles_connection_id", table_name="ai_model_profiles")
    op.drop_index("uq_ai_model_profiles_name_ci", table_name="ai_model_profiles")
    op.drop_table("ai_model_profiles")
    op.drop_index("uq_ai_provider_connections_name_ci", table_name="ai_provider_connections")
    op.drop_table("ai_provider_connections")


def _migrate_legacy_llm_config() -> None:
    bind = op.get_bind()
    legacy = bind.execute(
        sa.text(
            """
            SELECT value_json
            FROM system_configs
            WHERE category = 'llm'
              AND key = 'provider_config'
              AND organization_id IS NULL
            LIMIT 1
            """
        )
    ).mappings().first()
    if not legacy or not legacy["value_json"]:
        return

    config = legacy["value_json"]
    encrypted_key = config.get("encrypted_api_key")
    primary_model = _clean(config.get("model"))
    if not encrypted_key or not primary_model:
        return

    now = datetime.now(timezone.utc)
    provider = _provider_kind(config.get("provider"), config.get("endpoint"))
    endpoint = _endpoint(provider, config.get("endpoint"))
    connection_id = _get_or_create_connection(bind, provider, endpoint, encrypted_key, now)

    balanced_override = _clean(config.get("chat_balanced_model"))
    if balanced_override:
        primary_profile_id = _get_or_create_profile(
            bind,
            name="Default",
            connection_id=connection_id,
            model=primary_model,
            capabilities=None,
            enabled_for_chat=False,
            now=now,
            reuse_matching=False,
        )
        balanced_profile_id = _get_or_create_profile(
            bind,
            name=_clean(config.get("chat_balanced_label")) or "Balanced",
            connection_id=connection_id,
            model=balanced_override,
            capabilities=config.get("chat_balanced_capabilities"),
            enabled_for_chat=True,
            now=now,
            reuse_matching=False,
        )
    else:
        balanced_profile_id = _get_or_create_profile(
            bind,
            name=_clean(config.get("chat_balanced_label")) or "Balanced",
            connection_id=connection_id,
            model=primary_model,
            capabilities=config.get("chat_balanced_capabilities"),
            enabled_for_chat=True,
            now=now,
            reuse_matching=False,
        )
        primary_profile_id = balanced_profile_id

    _upsert_assignment(bind, "primary", primary_profile_id, now)
    _upsert_assignment(bind, "chat_default", balanced_profile_id, now)
    for assignment_key in ASSIGNMENT_KEYS - {"primary", "chat_default"}:
        _insert_assignment_if_missing(bind, assignment_key, primary_profile_id, now)

    fast_model = _clean(config.get("chat_fast_model"))
    if fast_model:
        _get_or_create_profile(
            bind,
            name=_clean(config.get("chat_fast_label")) or "Fast",
            connection_id=connection_id,
            model=fast_model,
            capabilities=config.get("chat_fast_capabilities"),
            enabled_for_chat=True,
            now=now,
            reuse_matching=False,
        )

    pro_model = _clean(config.get("chat_pro_model"))
    if pro_model:
        _get_or_create_profile(
            bind,
            name=_clean(config.get("chat_pro_label")) or "Pro",
            connection_id=connection_id,
            model=pro_model,
            capabilities=config.get("chat_pro_capabilities"),
            enabled_for_chat=True,
            now=now,
            reuse_matching=False,
        )

    for assignment_key, label, config_key in (
        ("summarization", "Summarization", "summarization_model"),
        ("tuning", "Tuning", "tuning_model"),
        ("image_generation", "Image Generation", "image_generation_model"),
        ("video_generation", "Video Generation", "video_generation_model"),
    ):
        model = _clean(config.get(config_key))
        if not model:
            continue
        profile_id = _find_matching_profile(bind, connection_id, model) or _get_or_create_profile(
            bind,
            name=label,
            connection_id=connection_id,
            model=model,
            capabilities=None,
            enabled_for_chat=False,
            now=now,
        )
        _upsert_assignment(bind, assignment_key, profile_id, now)


def _migrate_embedding_config() -> None:
    """Move dedicated embeddings onto a provider connection without making a profile."""
    bind = op.get_bind()
    legacy_embedding = bind.execute(
        sa.text(
            """
            SELECT value_json
            FROM system_configs
            WHERE category = 'llm'
              AND key = 'embedding_config'
              AND organization_id IS NULL
            LIMIT 1
            """
        )
    ).mappings().first()
    now = datetime.now(timezone.utc)

    if legacy_embedding and legacy_embedding["value_json"]:
        config = legacy_embedding["value_json"]
        encrypted_key = config.get("encrypted_api_key")
        model = _clean(config.get("model")) or "text-embedding-3-small"
        if not encrypted_key:
            return
        provider = _provider_kind("openai", config.get("endpoint"))
        endpoint = _endpoint(provider, config.get("endpoint"))
        dimensions = int(config.get("dimensions") or 1536)
        connection_id = _get_or_create_connection(
            bind,
            provider,
            endpoint,
            encrypted_key,
            now,
            name="Embeddings",
        )
        _upsert_embedding_config(bind, connection_id, model, dimensions, now)
        return

    legacy_llm = bind.execute(
        sa.text(
            """
            SELECT value_json
            FROM system_configs
            WHERE category = 'llm'
              AND key = 'provider_config'
              AND organization_id IS NULL
            LIMIT 1
            """
        )
    ).mappings().first()
    if not legacy_llm or not legacy_llm["value_json"]:
        return
    config = legacy_llm["value_json"]
    encrypted_key = config.get("encrypted_api_key")
    endpoint = _clean(config.get("endpoint"))
    if config.get("provider") != "openai" or endpoint or not encrypted_key:
        return
    connection_id = _get_or_create_connection(
        bind,
        "openai",
        None,
        encrypted_key,
        now,
        name="Default",
    )
    _upsert_embedding_config(bind, connection_id, "text-embedding-3-small", 1536, now)


def _migrate_ai_behavior() -> None:
    """Move agentless Chat instructions out of the legacy provider document."""
    bind = op.get_bind()
    legacy = bind.execute(
        sa.text(
            """
            SELECT value_json, created_by, updated_by
            FROM system_configs
            WHERE category = 'llm' AND key = 'provider_config'
              AND organization_id IS NULL
            LIMIT 1
            """
        )
    ).mappings().first()
    if not legacy or not legacy["value_json"]:
        return
    prompt = _clean(legacy["value_json"].get("default_system_prompt"))
    if not prompt:
        return

    existing = bind.execute(
        sa.text(
            """
            SELECT id FROM system_configs
            WHERE category = 'ai' AND key = 'behavior'
              AND organization_id IS NULL
            LIMIT 1
            """
        )
    ).first()
    now = datetime.now(timezone.utc)
    value = json.dumps({"default_system_prompt": prompt})
    if existing:
        bind.execute(
            sa.text(
                """
                UPDATE system_configs
                SET value_json = CAST(:value AS jsonb), updated_at = :now,
                    updated_by = :updated_by
                WHERE id = :id
                """
            ),
            {
                "value": value,
                "now": now,
                "updated_by": legacy["updated_by"],
                "id": existing[0],
            },
        )
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO system_configs
                (id, category, key, value_json, organization_id, created_at,
                 updated_at, created_by, updated_by)
            VALUES
                (:id, 'ai', 'behavior', CAST(:value AS jsonb), NULL, :now,
                 :now, :created_by, :updated_by)
            """
        ),
        {
            "id": uuid4(),
            "value": value,
            "now": now,
            "created_by": legacy["created_by"],
            "updated_by": legacy["updated_by"],
        },
    )


def _restore_legacy_ai_behavior() -> None:
    """Put Chat instructions back into the legacy document before downgrade."""
    bind = op.get_bind()
    behavior = bind.execute(
        sa.text(
            """
            SELECT value_json FROM system_configs
            WHERE category = 'ai' AND key = 'behavior'
              AND organization_id IS NULL
            LIMIT 1
            """
        )
    ).mappings().first()
    if behavior and behavior["value_json"]:
        bind.execute(
            sa.text(
                """
                UPDATE system_configs
                SET value_json = jsonb_set(
                    COALESCE(value_json, '{}'::jsonb),
                    '{default_system_prompt}', CAST(:prompt AS jsonb), true
                )
                WHERE category = 'llm' AND key = 'provider_config'
                  AND organization_id IS NULL
                """
            ),
            {
                "prompt": json.dumps(
                    behavior["value_json"].get("default_system_prompt")
                )
            },
        )
    bind.execute(
        sa.text(
            """
            DELETE FROM system_configs
            WHERE category = 'ai' AND key = 'behavior'
              AND organization_id IS NULL
            """
        )
    )


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _migrate_agent_profiles() -> None:
    """Preserve each distinct legacy agent model as one reusable profile."""
    bind = op.get_bind()
    models = [
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT DISTINCT btrim(llm_model)
                FROM agents
                WHERE llm_model IS NOT NULL AND btrim(llm_model) <> ''
                ORDER BY btrim(llm_model)
                """
            )
        ).all()
    ]
    if not models:
        return

    primary = bind.execute(
        sa.text(
            """
            SELECT profile.connection_id
            FROM ai_model_assignments AS assignment
            JOIN ai_model_profiles AS profile ON profile.id = assignment.profile_id
            WHERE assignment.assignment_key = 'primary'
            """
        )
    ).mappings().first()
    if primary is None:
        # A raw agent model was never runnable without a provider. Keep the
        # deployment moving; there is no valid provider connection to bind it to.
        return

    now = datetime.now(timezone.utc)
    for model in models:
        profile_id = bind.execute(
            sa.text(
                """
                SELECT id
                FROM ai_model_profiles
                WHERE connection_id = :connection_id AND model = :model
                ORDER BY created_at, id
                LIMIT 1
                """
            ),
            {"connection_id": primary["connection_id"], "model": model},
        ).scalar()
        if profile_id is None:
            profile_id = _get_or_create_profile(
                bind,
                name=f"Migrated · {model}",
                connection_id=primary["connection_id"],
                model=model,
                capabilities=None,
                enabled_for_chat=False,
                now=now,
                reuse_matching=False,
            )
        bind.execute(
            sa.text(
                """
                UPDATE agents
                SET llm_profile_id = :profile_id
                WHERE btrim(llm_model) = :model
                """
            ),
            {"profile_id": profile_id, "model": model},
        )


def _provider_kind(provider: object, endpoint: object) -> str:
    provider_value = provider if isinstance(provider, str) else "openai"
    endpoint_value = endpoint if isinstance(endpoint, str) else ""
    if "openrouter.ai" in endpoint_value:
        return "openrouter"
    if provider_value == "custom":
        return "openai_compatible"
    if provider_value in {"openai", "anthropic", "google", "openrouter", "openai_compatible"}:
        return provider_value
    return "openai"


def _endpoint(provider: str, endpoint: object) -> str | None:
    value = _clean(endpoint)
    if provider == "openrouter":
        return (value or OPENROUTER_DEFAULT_ENDPOINT).rstrip("/")
    return value.rstrip("/") if value else None


def _get_or_create_connection(bind, provider: str, endpoint: str | None, encrypted_key: str, now: datetime, *, name: str = "Default"):
    existing = bind.execute(
        sa.text("SELECT id FROM ai_provider_connections WHERE lower(name) = lower(:name)"),
        {"name": name},
    ).scalar()
    if existing:
        return existing
    connection_id = uuid4()
    bind.execute(
        sa.text(
            """
            INSERT INTO ai_provider_connections (id, name, provider, endpoint, encrypted_api_key, created_at, updated_at)
            VALUES (:id, :name, :provider, :endpoint, :encrypted_key, :created_at, :updated_at)
            """
        ),
        {
            "id": connection_id,
            "name": name,
            "provider": provider,
            "endpoint": endpoint,
            "encrypted_key": encrypted_key,
            "created_at": now,
            "updated_at": now,
        },
    )
    return connection_id


def _upsert_embedding_config(bind, connection_id, model: str, dimensions: int, now: datetime) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO ai_embedding_configs (key, connection_id, model, dimensions, created_at, updated_at)
            VALUES ('default', :connection_id, :model, :dimensions, :created_at, :updated_at)
            ON CONFLICT (key)
            DO UPDATE SET
                connection_id = EXCLUDED.connection_id,
                model = EXCLUDED.model,
                dimensions = EXCLUDED.dimensions,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "connection_id": connection_id,
            "model": model,
            "dimensions": dimensions,
            "created_at": now,
            "updated_at": now,
        },
    )


def _get_or_create_profile(
    bind,
    *,
    name: str,
    connection_id,
    model: str,
    capabilities: dict | None,
    enabled_for_chat: bool,
    now: datetime,
    reuse_matching: bool = True,
):
    if reuse_matching:
        existing = _find_matching_profile(bind, connection_id, model)
        if existing:
            return existing
    profile_name = _available_profile_name(bind, name)
    profile_id = uuid4()
    bind.execute(
        sa.text(
            """
            INSERT INTO ai_model_profiles (
                id, name, connection_id, model, capabilities, enabled_for_chat, created_at, updated_at
            )
            VALUES (
                :id, :name, :connection_id, :model, CAST(:capabilities AS jsonb), :enabled_for_chat, :created_at, :updated_at
            )
            """
        ),
        {
            "id": profile_id,
            "name": profile_name,
            "connection_id": connection_id,
            "model": model,
            "capabilities": json.dumps(capabilities) if capabilities is not None else None,
            "enabled_for_chat": enabled_for_chat,
            "created_at": now,
            "updated_at": now,
        },
    )
    return profile_id


def _find_matching_profile(bind, connection_id, model: str):
    return bind.execute(
        sa.text(
            """
            SELECT id
            FROM ai_model_profiles
            WHERE connection_id = :connection_id
              AND model = :model
            LIMIT 1
            """
        ),
        {"connection_id": connection_id, "model": model},
    ).scalar()


def _available_profile_name(bind, base_name: str) -> str:
    candidate = base_name[:100]
    suffix = 2
    while bind.execute(
        sa.text("SELECT 1 FROM ai_model_profiles WHERE lower(name) = lower(:name)"),
        {"name": candidate},
    ).scalar():
        suffix_text = f" {suffix}"
        candidate = f"{base_name[:100 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _upsert_assignment(bind, assignment_key: str, profile_id, now: datetime) -> None:
    if assignment_key not in ASSIGNMENT_KEYS:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO ai_model_assignments (assignment_key, profile_id, created_at, updated_at)
            VALUES (:assignment_key, :profile_id, :created_at, :updated_at)
            ON CONFLICT (assignment_key)
            DO UPDATE SET profile_id = EXCLUDED.profile_id, updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "assignment_key": assignment_key,
            "profile_id": profile_id,
            "created_at": now,
            "updated_at": now,
        },
    )


def _insert_assignment_if_missing(bind, assignment_key: str, profile_id, now: datetime) -> None:
    if assignment_key not in ASSIGNMENT_KEYS:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO ai_model_assignments (assignment_key, profile_id, created_at, updated_at)
            VALUES (:assignment_key, :profile_id, :created_at, :updated_at)
            ON CONFLICT (assignment_key) DO NOTHING
            """
        ),
        {
            "assignment_key": assignment_key,
            "profile_id": profile_id,
            "created_at": now,
            "updated_at": now,
        },
    )
