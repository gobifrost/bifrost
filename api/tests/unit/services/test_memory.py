"""Tests for private-memory settings, ownership, and semantic retrieval."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm import MemoryStore, User
from src.services.memory import MemoryDisabledError, MemoryService


class _FakeEmbedder:
    async def embed_single(self, text: str) -> list[float]:
        return [1.0, 0.0] if "acme" in text.casefold() else [0.0, 1.0]


async def _user(db_session, email: str) -> User:
    user = User(
        id=uuid4(),
        email=email,
        name=email,
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_memory_is_on_for_users_by_default_when_platform_enables_it(db_session):
    user = await _user(db_session, "memory-settings@example.com")
    service = MemoryService(db_session, user_id=user.id, organization_id=None)

    assert await service.settings() == {
        "platform_enabled": False,
        "user_enabled": True,
        "effective_enabled": False,
    }
    await service.set_platform_enabled(True, updated_by="admin@example.com")

    assert (await service.settings())["effective_enabled"] is True

    await service.set_user_enabled(False)
    with pytest.raises(MemoryDisabledError):
        await service.save("This should not be stored.", {})


@pytest.mark.asyncio
async def test_memory_save_search_and_remove_stay_in_the_user_store(
    db_session,
    monkeypatch,
):
    first_user = await _user(db_session, "memory-first@example.com")
    second_user = await _user(db_session, "memory-second@example.com")
    first = MemoryService(db_session, user_id=first_user.id, organization_id=None)
    second = MemoryService(db_session, user_id=second_user.id, organization_id=None)
    embedder = _FakeEmbedder()

    async def fake_embedding_client(_session):
        return embedder

    monkeypatch.setattr(
        "src.services.memory.get_embedding_client",
        fake_embedding_client,
    )

    await first.set_platform_enabled(True, updated_by="admin@example.com")

    acme = await first.save(
        "Acme onboarding uses the customer checklist.",
        {"customer": "acme"},
    )
    await first.save("Beta prefers monthly reviews.", {"customer": "beta"})

    matches = await first.search("What is Acme's onboarding process?", limit=2)
    assert matches[0][0].id == acme.id
    assert matches[0][0].doc_metadata == {"customer": "acme"}
    assert matches[0][1] == pytest.approx(1.0)

    second_entries = await second.list_entries()
    second_removed = await second.remove(acme.id)
    first_removed = await first.remove(acme.id)
    first_entries = await first.list_entries()
    assert second_entries == []
    assert second_removed is False
    assert first_removed is True
    assert all(entry.id != acme.id for entry in first_entries)

    stores = (
        await db_session.execute(
            select(MemoryStore).where(
                MemoryStore.user_id.in_([first_user.id, second_user.id])
            )
        )
    ).scalars().all()
    assert {store.user_id for store in stores} == {first_user.id}
