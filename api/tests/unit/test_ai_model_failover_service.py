"""Failover pointer lifecycle on AI model profiles.

Covers create/update validation (missing target, self-reference, cycles,
chain length), chain resolution order, the delete guard, and merge
successor semantics. Uses the shared ``db_session`` fixture.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from src.services.ai_model_service import (
    AIModelService,
    MAX_FAILOVER_CHAIN_LENGTH,
)


async def _connection(service: AIModelService, name: str = "Default"):
    return await service.create_connection(
        name=f"{name} {uuid4().hex[:8]}",
        provider="openrouter",
        api_key="sk-test",
        endpoint=None,
    )


async def _profile(service, connection, name: str, **kwargs):
    return await service.create_profile(
        name=f"{name} {uuid4().hex[:8]}",
        connection_id=connection.id,
        model="openai/gpt-4o-mini",
        capabilities=None,
        enabled_for_chat=False,
        **kwargs,
    )


async def _pair(service):
    connection = await _connection(service)
    first = await _profile(service, connection, "First")
    return connection, first


@pytest.mark.asyncio
async def test_create_with_fallback_links_profiles(db_session):
    service = AIModelService(db_session)
    connection, primary = await _pair(service)
    fallback = await _profile(service, connection, "Fallback")

    linked = await service.create_profile(
        name=f"Linked {uuid4().hex[:8]}",
        connection_id=connection.id,
        model="openai/gpt-4o-mini",
        capabilities=None,
        enabled_for_chat=False,
        failover_profile_id=fallback.id,
    )
    assert linked.failover_profile_id == fallback.id

    fetched = await service.get_profile(linked.id)
    assert fetched.failover_profile.name == fallback.name
    assert primary.failover_profile_id is None


@pytest.mark.asyncio
async def test_create_with_missing_fallback_raises(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    with pytest.raises(LookupError):
        await service.create_profile(
            name="Orphan",
            connection_id=connection.id,
            model="openai/gpt-4o-mini",
            capabilities=None,
            enabled_for_chat=False,
            failover_profile_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_update_set_and_clear_fallback(db_session):
    service = AIModelService(db_session)
    connection, primary = await _pair(service)
    fallback = await _profile(service, connection, "Fallback")

    updated = await service.update_profile(
        primary.id,
        failover_profile_id=fallback.id,
        failover_profile_id_provided=True,
    )
    assert updated.failover_profile_id == fallback.id

    cleared = await service.update_profile(
        primary.id,
        failover_profile_id=None,
        failover_profile_id_provided=True,
    )
    assert cleared.failover_profile_id is None


@pytest.mark.asyncio
async def test_update_without_provided_flag_leaves_fallback(db_session):
    service = AIModelService(db_session)
    connection, primary = await _pair(service)
    fallback = await _profile(service, connection, "Fallback")
    await service.update_profile(
        primary.id,
        failover_profile_id=fallback.id,
        failover_profile_id_provided=True,
    )
    untouched = await service.update_profile(primary.id, name="Renamed")
    assert untouched.failover_profile_id == fallback.id


@pytest.mark.asyncio
async def test_self_reference_rejected(db_session):
    service = AIModelService(db_session)
    _, primary = await _pair(service)
    with pytest.raises(ValueError, match="itself"):
        await service.update_profile(
            primary.id,
            failover_profile_id=primary.id,
            failover_profile_id_provided=True,
        )


@pytest.mark.asyncio
async def test_direct_cycle_rejected(db_session):
    service = AIModelService(db_session)
    connection, first = await _pair(service)
    second = await _profile(service, connection, "Second")
    await service.update_profile(
        first.id,
        failover_profile_id=second.id,
        failover_profile_id_provided=True,
    )
    with pytest.raises(ValueError, match="cycle"):
        await service.update_profile(
            second.id,
            failover_profile_id=first.id,
            failover_profile_id_provided=True,
        )


@pytest.mark.asyncio
async def test_indirect_cycle_rejected(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    first = await _profile(service, connection, "First")
    second = await _profile(service, connection, "Second")
    third = await _profile(service, connection, "Third")
    await service.update_profile(
        first.id, failover_profile_id=second.id, failover_profile_id_provided=True
    )
    await service.update_profile(
        second.id, failover_profile_id=third.id, failover_profile_id_provided=True
    )
    with pytest.raises(ValueError, match="cycle"):
        await service.update_profile(
            third.id, failover_profile_id=first.id, failover_profile_id_provided=True
        )


@pytest.mark.asyncio
async def test_chain_longer_than_max_rejected(db_session):
    assert MAX_FAILOVER_CHAIN_LENGTH == 3
    service = AIModelService(db_session)
    connection = await _connection(service)
    profiles = [await _profile(service, connection, f"P{i}") for i in range(4)]
    await service.update_profile(
        profiles[0].id,
        failover_profile_id=profiles[1].id,
        failover_profile_id_provided=True,
    )
    await service.update_profile(
        profiles[1].id,
        failover_profile_id=profiles[2].id,
        failover_profile_id_provided=True,
    )
    with pytest.raises(ValueError, match="exceed"):
        await service.update_profile(
            profiles[2].id,
            failover_profile_id=profiles[3].id,
            failover_profile_id_provided=True,
        )


@pytest.mark.asyncio
async def test_resolve_chain_returns_primary_then_fallbacks(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    first = await _profile(service, connection, "First")
    second = await _profile(service, connection, "Second")
    await service.update_profile(
        first.id, failover_profile_id=second.id, failover_profile_id_provided=True
    )

    chain = await service.resolve_chain(profile_id=first.id)
    assert [config.model for config in chain] == [
        "openai/gpt-4o-mini",
        "openai/gpt-4o-mini",
    ]
    assert chain[0].api_key == "sk-test"

    single = await service.resolve_chain(profile_id=second.id)
    assert len(single) == 1


@pytest.mark.asyncio
async def test_resolve_chain_stops_at_legacy_cycle(db_session):
    service = AIModelService(db_session)
    connection, first = await _pair(service)
    second = await _profile(service, connection, "Second")
    # Bypass validation to simulate pre-constraint rows.
    first.failover_profile_id = second.id
    second.failover_profile_id = first.id
    await db_session.flush()

    chain = await service.resolve_chain(profile_id=first.id)
    assert len(chain) == 2


@pytest.mark.asyncio
async def test_resolve_chain_truncates_legacy_long_chain(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    profiles = [await _profile(service, connection, f"P{i}") for i in range(4)]
    await service.update_profile(
        profiles[0].id,
        failover_profile_id=profiles[1].id,
        failover_profile_id_provided=True,
    )
    await service.update_profile(
        profiles[1].id,
        failover_profile_id=profiles[2].id,
        failover_profile_id_provided=True,
    )
    # Bypass validation to simulate a pre-constraint fourth hop.
    profiles[2].failover_profile_id = profiles[3].id
    await db_session.flush()

    chain = await service.resolve_chain(profile_id=profiles[0].id)
    assert len(chain) == MAX_FAILOVER_CHAIN_LENGTH


@pytest.mark.asyncio
async def test_delete_guarded_while_used_as_fallback(db_session):
    service = AIModelService(db_session)
    connection, primary = await _pair(service)
    fallback = await _profile(service, connection, "Fallback")
    await service.update_profile(
        primary.id, failover_profile_id=fallback.id, failover_profile_id_provided=True
    )
    with pytest.raises(ValueError, match=primary.name):
        await service.delete_profile(fallback.id)

    await service.update_profile(
        primary.id, failover_profile_id=None, failover_profile_id_provided=True
    )
    await service.delete_profile(fallback.id)
    with pytest.raises(LookupError):
        await service.get_profile(fallback.id)


@pytest.mark.asyncio
async def test_merge_repoints_inbound_fallbacks_to_target(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    inbound = await _profile(service, connection, "Inbound")
    source = await _profile(service, connection, "Source")
    target = await _profile(service, connection, "Target")
    await service.update_profile(
        inbound.id, failover_profile_id=source.id, failover_profile_id_provided=True
    )

    result = await service.merge_profiles(
        profile_ids=[source.id, target.id], target_profile_id=target.id
    )
    assert result.profile.id == target.id

    refreshed = await service.get_profile(inbound.id)
    assert refreshed.failover_profile_id == target.id


@pytest.mark.asyncio
async def test_merge_target_inherits_deleted_source_fallback(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    source = await _profile(service, connection, "Source")
    target = await _profile(service, connection, "Target")
    anchor = await _profile(service, connection, "Anchor")
    await service.update_profile(
        source.id, failover_profile_id=anchor.id, failover_profile_id_provided=True
    )
    await service.update_profile(
        target.id, failover_profile_id=source.id, failover_profile_id_provided=True
    )

    await service.merge_profiles(
        profile_ids=[source.id, target.id], target_profile_id=target.id
    )
    refreshed = await service.get_profile(target.id)
    assert refreshed.failover_profile_id == anchor.id


@pytest.mark.asyncio
async def test_merge_repointing_into_cycle_raises(db_session):
    service = AIModelService(db_session)
    connection = await _connection(service)
    # Target -> X -> Source; merging Source into Target would repoint X -> Target,
    # closing a cycle, so the merge must refuse.
    target = await _profile(service, connection, "Target")
    other = await _profile(service, connection, "Other")
    referrer = await _profile(service, connection, "Referrer")
    source = await _profile(service, connection, "Source")
    await service.update_profile(
        target.id, failover_profile_id=referrer.id, failover_profile_id_provided=True
    )
    await service.update_profile(
        referrer.id, failover_profile_id=source.id, failover_profile_id_provided=True
    )
    with pytest.raises(ValueError, match="cycle"):
        await service.merge_profiles(
            profile_ids=[source.id, other.id, target.id],
            target_profile_id=target.id,
        )


# ==================== migration up/down ====================


FAILOVER_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260918_profile_failover.py"
)


def _load_failover_migration():
    spec = importlib.util.spec_from_file_location(
        "_profile_failover_migration", FAILOVER_MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FailoverOpStub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_column(self, table: str, column) -> None:
        self.calls.append(("add_column", table, column.name))

    def create_foreign_key(self, name, source, referent, *args, **kwargs) -> None:
        self.calls.append(("create_foreign_key", name, source, referent))

    def create_index(self, name: str, table: str, columns) -> None:
        self.calls.append(("create_index", name, table))

    def drop_index(self, name: str, table_name: str | None = None) -> None:
        self.calls.append(("drop_index", name, table_name))

    def drop_constraint(self, name: str, table: str, type_: str | None = None) -> None:
        self.calls.append(("drop_constraint", name, table, type_))

    def drop_column(self, table: str, column: str) -> None:
        self.calls.append(("drop_column", table, column))


def test_failover_migration_up_adds_pointer_fk_and_index(monkeypatch) -> None:
    migration = _load_failover_migration()
    assert migration.down_revision == "20260918_merge_plat_prof_heads"
    stub = _FailoverOpStub()
    monkeypatch.setattr(migration, "op", stub)
    migration.upgrade()
    assert ("add_column", "ai_model_profiles", "failover_profile_id") in stub.calls
    assert (
        "create_foreign_key",
        "fk_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
        "ai_model_profiles",
    ) in stub.calls
    assert (
        "create_index",
        "ix_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
    ) in stub.calls


def test_failover_migration_downgrade_reverses(monkeypatch) -> None:
    migration = _load_failover_migration()
    stub = _FailoverOpStub()
    monkeypatch.setattr(migration, "op", stub)
    migration.downgrade()
    assert (
        "drop_index",
        "ix_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
    ) in stub.calls
    assert (
        "drop_constraint",
        "fk_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
        "foreignkey",
    ) in stub.calls
    assert ("drop_column", "ai_model_profiles", "failover_profile_id") in stub.calls


# ==================== chain resolution resilience ====================


async def _dead_compatible_connection(service):
    return await service.create_connection(
        name=f"Dead {uuid4().hex[:8]}",
        provider="openai_compatible",
        api_key="dead-key",
        endpoint="http://127.0.0.1:9/v1",
    )


def _block_transport_detection(monkeypatch):
    async def _boom(*args, **kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(
        "src.services.openai_transport_detection.detect_openai_transport",
        _boom,
    )


@pytest.mark.asyncio
async def test_resolve_chain_skips_dead_primary(db_session, monkeypatch):
    """Transport detection runs at resolve time; a dead primary endpoint must
    not wedge the chain — the fallback still resolves."""
    _block_transport_detection(monkeypatch)
    service = AIModelService(db_session)
    dead = await _dead_compatible_connection(service)
    good = await _connection(service)
    primary = await _profile(service, dead, "Primary")
    fallback = await _profile(service, good, "Fallback")
    await service.update_profile(
        primary.id, failover_profile_id=fallback.id, failover_profile_id_provided=True
    )

    chain = await service.resolve_chain(profile_id=primary.id)
    assert len(chain) == 1
    assert chain[0].endpoint == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_resolve_chain_all_dead_raises_head_error(db_session, monkeypatch):
    _block_transport_detection(monkeypatch)
    service = AIModelService(db_session)
    dead = await _dead_compatible_connection(service)
    primary = await _profile(service, dead, "Primary")
    fallback = await _profile(service, dead, "Fallback")
    await service.update_profile(
        primary.id, failover_profile_id=fallback.id, failover_profile_id_provided=True
    )

    with pytest.raises(ConnectionError, match="refused"):
        await service.resolve_chain(profile_id=primary.id)
