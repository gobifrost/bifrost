"""One real workflow exercises the provider import boundary in the SDK host.

Run explicitly with the host-side worker memory probe. The provider is local,
deterministic, and free; timings are diagnostic rather than CI thresholds.
"""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from tests.e2e.conftest import E2E_API_URL, execute_workflow_sync, write_and_register

pytestmark = pytest.mark.slow


@pytest.fixture(scope="session")
def e2e_client():
    with httpx.Client(base_url=E2E_API_URL, timeout=60) as client:
        yield client


def test_real_sdk_ai_calls_use_shared_api(e2e_client, platform_admin, async_session_factory):
    tag = uuid4().hex[:12]
    name = f"sdk_ai_footprint_{tag}"
    path = f"{name}.py"
    connection_id = profile_id = None

    async def set_transport():
        async with async_session_factory() as db:
            profile = await db.get(AIModelProfile, profile_id)
            assert profile is not None
            profile.openai_transport = "chat_completions"
            await db.commit()

    async def cleanup():
        async with async_session_factory() as db:
            if profile_id is not None:
                await db.execute(delete(AIModelAssignment).where(
                    AIModelAssignment.profile_id == profile_id
                ))
                await db.execute(delete(AIModelProfile).where(AIModelProfile.id == profile_id))
            if connection_id is not None:
                await db.execute(delete(AIProviderConnection).where(
                    AIProviderConnection.id == connection_id
                ))
            await db.commit()

    try:
        response = e2e_client.post("/api/admin/ai/connections", headers=platform_admin.headers, json={
            "name": name, "provider": "openai_compatible", "api_key": "fixture-key",
            "endpoint": "http://scheduler-fixtures:8080/v1",
        })
        assert response.status_code == 201, response.text
        connection_id = UUID(response.json()["id"])
        response = e2e_client.post("/api/admin/ai/profiles", headers=platform_admin.headers, json={
            "name": name, "connection_id": str(connection_id), "model": "fixture-chat",
        })
        assert response.status_code == 201, response.text
        profile_id = UUID(response.json()["id"])
        asyncio.run(set_transport())

        content = f'''from bifrost import workflow
from bifrost.ai import ai
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="SDK provider footprint probe")
async def {name}():
    import time
    timings = []
    for _ in range(2):
        started = time.perf_counter()
        response = await ai.complete("Reply exactly ok.", profile="{name}")
        assert response.content == "ok"
        timings.append(round(time.perf_counter() - started, 3))
    return {{"used_socket": get_engine_socket_path() is not None, "call_seconds": timings}}
'''
        registered = write_and_register(
            e2e_client, platform_admin.headers, path, content, name,
        )
        result = execute_workflow_sync(
            e2e_client, platform_admin.headers, registered["id"], max_wait=120,
        )
        assert result["status"] == "Success", result
        assert len(result["result"]["call_seconds"]) == 2
        print("SDK_AI_FOOTPRINT_BENCHMARK " + json.dumps(result["result"], sort_keys=True))
    finally:
        e2e_client.delete(f"/api/files/editor?path={path}", headers=platform_admin.headers)
        asyncio.run(cleanup())
