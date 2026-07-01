"""The same bundle (with an event subscription) must install into two installs
without a duplicate-PK collision — the subscription's own id must be remapped
per install (audit HIGH)."""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer, solution_entity_id


pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


async def _make_solution(db, prefix: str) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"{prefix}-{uuid.uuid4().hex[:8]}",
        name=f"Event Sub Remap Test ({prefix})",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    return sol


@pytest.mark.asyncio
async def test_event_subscription_id_remapped_per_install(db_session):
    sol_a = await _make_solution(db_session, "sub-remap-a")
    sol_b = await _make_solution(db_session, "sub-remap-b")

    sub_id = "33333333-3333-3333-3333-333333333333"
    src_id = "22222222-2222-2222-2222-222222222222"

    def bundle(sol):
        return SolutionBundle(
            solution=sol,
            version="0.1.0",
            events=[{
                "id": src_id,
                "name": "nightly",
                "source_type": "schedule",
                "is_active": True,
                "schedule": {"cron": "0 0 * * *", "timezone": "UTC"},
                "subscriptions": [{"id": sub_id, "target_type": "workflow", "is_active": True}],
            }],
        )

    await SolutionDeployer(db_session).deploy(bundle(sol_a), force=True)
    # Must NOT raise a duplicate-PK IntegrityError:
    await SolutionDeployer(db_session).deploy(bundle(sol_b), force=True)
    # And the two installs hold DISTINCT subscription ids:
    assert solution_entity_id(sol_a.id, uuid.UUID(sub_id)) != solution_entity_id(sol_b.id, uuid.UUID(sub_id))


@pytest.mark.asyncio
async def test_topic_event_source_install_round_trips_event_type(db_session):
    """A topic EventSource installed via a solution bundle must carry its event_type
    (topic routing key) so the dispatcher's get_by_topic() can resolve it.

    Regression for B1: ManifestEventSource carried no parent event_type, so install
    wrote event_type=NULL and topic triggers silently never fired."""
    from src.models.orm.events import EventSource
    from src.repositories.events import EventSourceRepository

    sol = await _make_solution(db_session, "topic-install")
    src_id = "44444444-4444-4444-4444-444444444444"

    bundle = SolutionBundle(
        solution=sol,
        version="0.1.0",
        events=[{
            "id": src_id,
            "name": "on ticket created",
            "source_type": "topic",
            "event_type": "ticket.created",
            "is_active": True,
            "subscriptions": [],
        }],
    )
    await SolutionDeployer(db_session).deploy(bundle, force=True)

    installed_id = solution_entity_id(sol.id, uuid.UUID(src_id))
    es = await db_session.get(EventSource, installed_id)
    assert es is not None
    assert es.event_type == "ticket.created"

    found = await EventSourceRepository(db_session).get_by_topic(
        "ticket.created", solution_id=sol.id
    )
    assert found is not None
    assert found.id == installed_id


@pytest.mark.asyncio
async def test_topic_lookup_targets_solution_install_not_sibling(db_session):
    from src.models.orm.events import EventSource
    from src.repositories.events import EventSourceRepository

    sol_a = await _make_solution(db_session, "topic-a")
    sol_b = await _make_solution(db_session, "topic-b")
    topic = f"ticket.{uuid.uuid4().hex[:8]}"
    src_id = "55555555-5555-5555-5555-555555555555"

    def bundle(sol):
        return SolutionBundle(
            solution=sol,
            version="0.1.0",
            events=[{
                "id": src_id,
                "name": "on ticket changed",
                "source_type": "topic",
                "event_type": topic,
                "is_active": True,
                "subscriptions": [],
            }],
        )

    await SolutionDeployer(db_session).deploy(bundle(sol_a), force=True)
    await SolutionDeployer(db_session).deploy(bundle(sol_b), force=True)

    repo = EventSourceRepository(db_session)
    found_b = await repo.get_by_topic(topic, solution_id=sol_b.id)
    assert found_b is not None
    assert found_b.id == solution_entity_id(sol_b.id, uuid.UUID(src_id))

    loose = await repo.get_by_topic(topic)
    assert loose is None

    assert await db_session.get(EventSource, solution_entity_id(sol_a.id, uuid.UUID(src_id)))
    assert await db_session.get(EventSource, solution_entity_id(sol_b.id, uuid.UUID(src_id)))


@pytest.mark.asyncio
async def test_topic_emit_targets_solution_install_not_sibling(db_session):
    from src.models.orm.events import Event
    from src.services.events.processor import EventProcessor

    sol_a = await _make_solution(db_session, "emit-a")
    sol_b = await _make_solution(db_session, "emit-b")
    topic = f"ticket.{uuid.uuid4().hex[:8]}"
    src_id = "66666666-6666-6666-6666-666666666666"

    def bundle(sol):
        return SolutionBundle(
            solution=sol,
            version="0.1.0",
            events=[{
                "id": src_id,
                "name": "on ticket emitted",
                "source_type": "topic",
                "event_type": topic,
                "is_active": True,
                "subscriptions": [],
            }],
        )

    await SolutionDeployer(db_session).deploy(bundle(sol_a), force=True)
    await SolutionDeployer(db_session).deploy(bundle(sol_b), force=True)

    event_id, subscribers = await EventProcessor(db_session).emit_topic(
        topic=topic,
        data={"ticket_id": "T-1"},
        solution_id=sol_b.id,
    )

    event = await db_session.get(Event, event_id)
    assert event is not None
    assert subscribers == 0
    assert event.event_source_id == solution_entity_id(sol_b.id, uuid.UUID(src_id))
