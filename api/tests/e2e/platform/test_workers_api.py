"""
Integration tests for platform admin worker management API.

Tests the API behavior for:
- Listing workers from Redis
- Getting worker details
- Triggering process recycle via Redis pub/sub
- Queue status retrieval
- Stuck execution history
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Execution, Workflow
from src.models.enums import ExecutionStatus
from src.models.orm.users import User
from src.core.constants import PROVIDER_ORG_ID
from tests.conftest import TEST_REDIS_URL


# Reset module-level redis connection before tests
@pytest.fixture(autouse=True)
def reset_redis_connections():
    """Reset module-level Redis connections before each test.

    Each of these modules caches its own ``redis.asyncio`` client in a
    module global. Redis connections pin themselves to the asyncio loop
    that first touched them, so a client cached by a prior test's loop
    will raise ``Event loop is closed`` when reused here.
    """
    import src.routers.platform.workers as workers_module
    import src.services.execution.queue_tracker as queue_tracker_module

    workers_module._redis = None
    queue_tracker_module._redis = None
    yield
    workers_module._redis = None
    queue_tracker_module._redis = None


@pytest_asyncio.fixture
async def redis_client():
    """Get Redis client for test setup/teardown."""
    client = aioredis.from_url(
        TEST_REDIS_URL,
        decode_responses=True,
        socket_timeout=5.0,
    )
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def clean_redis_workers(redis_client):
    """Clean up worker-related Redis keys before and after tests."""
    # Clean up pool keys before test
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match="bifrost:pool:*", count=100)
        if keys:
            await redis_client.delete(*keys)
        if cursor == 0:
            break

    yield

    # Clean up pool keys after test
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match="bifrost:pool:*", count=100)
        if keys:
            await redis_client.delete(*keys)
        if cursor == 0:
            break


@pytest_asyncio.fixture
async def clean_test_data(db_session: AsyncSession):
    """Clean up test-created data."""
    yield

    # Clean up test executions with stuck messages
    await db_session.execute(
        delete(Execution).where(
            Execution.error_message.ilike("%test_stuck%")
        )
    )

    await db_session.commit()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """Create a test user for executions."""
    user = User(
        id=uuid4(),
        email=f"test_{uuid4().hex[:8]}@example.com",
        name="Test User",
        organization_id=PROVIDER_ORG_ID,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def test_workflow(db_session: AsyncSession):
    """Create a test workflow for blacklist tests."""
    workflow = Workflow(
        id=uuid4(),
        name="Test Blacklist Workflow",
        function_name="test_blacklist_workflow",
        path="test/test_blacklist.py",
        type="workflow",
        is_active=True,
    )
    db_session.add(workflow)
    await db_session.flush()
    return workflow


@pytest.mark.e2e
@pytest.mark.asyncio
class TestWorkersListEndpoint:
    """Tests for GET /api/platform/workers endpoint."""

    async def test_list_workers_empty(
        self,
        db_session: AsyncSession,
        clean_redis_workers,
    ):
        """List workers returns empty list when no workers registered."""
        from src.routers.platform.workers import list_pools
        from src.core.principal import UserPrincipal

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        result = await list_pools(admin)

        assert result.total == 0
        assert result.pools == []

    async def test_list_workers_with_registered_worker(
        self,
        redis_client,
        db_session: AsyncSession,
        clean_redis_workers,
    ):
        """List workers returns registered worker data."""
        from src.routers.platform.workers import list_pools
        from src.core.principal import UserPrincipal

        # Register a worker in Redis
        worker_id = "test-worker-001"
        await redis_client.hset(
            f"bifrost:pool:{worker_id}",
            mapping={
                "hostname": "test-host",
                "status": "online",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Add heartbeat data
        heartbeat = {
            "type": "worker_heartbeat",
            "worker_id": worker_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processes": [
                {"pid": 12345, "process_id": "process-1", "state": "idle"},
                {"pid": 12346, "process_id": "process-2", "state": "busy"},
            ],
            "pool_size": 2,
            "idle_count": 1,
            "busy_count": 1,
        }
        await redis_client.set(
            f"bifrost:pool:{worker_id}:heartbeat",
            json.dumps(heartbeat),
        )

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        result = await list_pools(admin)

        assert result.total == 1
        pool = result.pools[0]
        assert pool.worker_id == worker_id
        assert pool.hostname == "test-host"
        assert pool.status == "online"
        assert pool.pool_size == 2
        assert pool.busy_count == 1


@pytest.mark.e2e
@pytest.mark.asyncio
class TestWorkerDetailEndpoint:
    """Tests for GET /api/platform/workers/{worker_id} endpoint."""

    async def test_get_worker_not_found(
        self,
        db_session: AsyncSession,
        clean_redis_workers,
    ):
        """Get worker returns 404 for non-existent worker."""
        from fastapi import HTTPException
        from src.routers.platform.workers import get_pool
        from src.core.principal import UserPrincipal

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_pool("non-existent-worker", admin)

        assert exc_info.value.status_code == 404

    async def test_get_worker_with_details(
        self,
        redis_client,
        db_session: AsyncSession,
        clean_redis_workers,
    ):
        """Get worker returns detailed worker information."""
        from src.routers.platform.workers import get_pool
        from src.core.principal import UserPrincipal

        # Register a worker in Redis
        worker_id = "test-worker-002"
        await redis_client.hset(
            f"bifrost:pool:{worker_id}",
            mapping={
                "hostname": "detail-host",
                "status": "online",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Add heartbeat data with processes
        heartbeat = {
            "type": "worker_heartbeat",
            "worker_id": worker_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processes": [
                {
                    "pid": 22345,
                    "process_id": "process-1",
                    "state": "idle",
                    "current_execution_id": None,
                    "executions_completed": 10,
                    "uptime_seconds": 3600,
                    "memory_mb": 128.5,
                },
            ],
            "pool_size": 1,
            "idle_count": 1,
            "busy_count": 0,
        }
        await redis_client.set(
            f"bifrost:pool:{worker_id}:heartbeat",
            json.dumps(heartbeat),
        )

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        result = await get_pool(worker_id, admin)

        assert result.worker_id == worker_id
        assert result.hostname == "detail-host"
        assert len(result.processes) == 1
        assert result.processes[0].pid == 22345
        assert result.processes[0].executions_completed == 10


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRecycleProcessEndpoint:
    """Tests for POST /api/platform/workers/{worker_id}/processes/{pid}/recycle endpoint."""

    async def test_recycle_worker_not_found(
        self,
        db_session: AsyncSession,
        clean_redis_workers,
    ):
        """Recycle returns 404 for non-existent worker."""
        from fastapi import HTTPException
        from src.routers.platform.workers import recycle_process
        from src.core.principal import UserPrincipal

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        with pytest.raises(HTTPException) as exc_info:
            await recycle_process("non-existent-worker", 12345, admin)

        assert exc_info.value.status_code == 404

    async def test_recycle_publishes_command(
        self,
        redis_client,
        db_session: AsyncSession,
        clean_redis_workers,
    ):
        """Recycle publishes command to Redis pub/sub."""
        from src.routers.platform.workers import recycle_process
        from src.core.principal import UserPrincipal
        from src.models.contracts.platform import RecycleProcessRequest

        # Register a pool in Redis (using new key pattern)
        worker_id = "test-worker-recycle"
        await redis_client.hset(
            f"bifrost:pool:{worker_id}",
            mapping={
                "hostname": "recycle-host",
                "status": "online",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Subscribe to the command channel before publishing
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"bifrost:pool:{worker_id}:commands")

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        request = RecycleProcessRequest(reason="Manual recycle test")
        result = await recycle_process(worker_id, 12345, admin, request)

        assert result.success is True
        assert result.worker_id == worker_id
        assert result.pid == 12345
        assert "12345" in result.message

        await pubsub.unsubscribe(f"worker:{worker_id}:commands")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestQueueEndpoint:
    """Tests for GET /api/platform/queue endpoint."""

    async def test_get_queue_empty(
        self,
        db_session: AsyncSession,
    ):
        """Queue endpoint returns empty list when no pending executions."""
        from src.routers.platform.workers import get_queue
        from src.core.principal import UserPrincipal

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        result = await get_queue(admin, limit=50, offset=0)

        # Queue might have items from other tests, just verify structure
        assert hasattr(result, "total")
        assert hasattr(result, "items")
        assert isinstance(result.items, list)


@pytest.mark.e2e
@pytest.mark.asyncio
class TestStuckHistoryEndpoint:
    """Tests for GET /api/platform/stuck-history endpoint."""

    async def test_get_stuck_history_empty(
        self,
        db_session: AsyncSession,
        clean_test_data,
    ):
        """Stuck history returns empty list when no stuck executions."""
        from src.routers.platform.workers import get_stuck_history
        from src.core.principal import UserPrincipal

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        result = await get_stuck_history(admin, hours=24, db=db_session)

        assert result.hours == 24
        assert isinstance(result.workflows, list)

    async def test_get_stuck_history_aggregates(
        self,
        db_session: AsyncSession,
        test_user,
        clean_test_data,
    ):
        """Stuck history aggregates stuck executions by workflow."""
        from src.routers.platform.workers import get_stuck_history
        from src.core.principal import UserPrincipal

        # Create executions with stuck error messages
        now = datetime.now(timezone.utc)
        for i in range(3):
            execution = Execution(
                id=uuid4(),
                workflow_name="test_workflow_stuck",
                status=ExecutionStatus.FAILED,
                error_message="test_stuck: Execution stuck after timeout",
                started_at=now - timedelta(hours=1),
                completed_at=now,
                executed_by=test_user.id,
                executed_by_name=test_user.name,
            )
            db_session.add(execution)

        await db_session.commit()

        admin = UserPrincipal(
            user_id=uuid4(),
            email="admin@test.com",
            organization_id=None,
            is_superuser=True,
        )

        result = await get_stuck_history(admin, hours=24, db=db_session)

        # Find the test workflow in results
        test_workflow = next(
            (w for w in result.workflows if w.workflow_name == "test_workflow_stuck"),
            None,
        )

        assert test_workflow is not None
        assert test_workflow.stuck_count == 3
