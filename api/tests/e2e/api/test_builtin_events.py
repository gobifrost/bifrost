"""E2E coverage for Bifrost built-in topic emitters."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.auth import ExecutionContext, UserPrincipal
from src.core.security import encrypt_secret
from src.models.orm import OAuthProvider, OAuthToken
from src.models.orm.integrations import IntegrationMapping
from tests.e2e.conftest import execute_workflow_sync, poll_until, write_and_register


RECORDER_WORKFLOW = '''"""Built-in event recorder workflow."""
from bifrost import workflow

@workflow(name="e2e_builtin_event_recorder")
async def e2e_builtin_event_recorder(_event: dict) -> dict:
    return {
        "topic": _event.get("type"),
        "body": _event.get("body"),
    }
'''

FAILING_WORKFLOW = '''"""Built-in event failing workflow."""
from bifrost import workflow

@workflow(name="e2e_builtin_event_fails")
async def e2e_builtin_event_fails() -> dict:
    raise RuntimeError("intentional built-in event failure")
'''


@pytest.fixture(scope="module")
def recorder_workflow(e2e_client, platform_admin):
    workflow = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_builtin_event_recorder.py",
        RECORDER_WORKFLOW,
        "e2e_builtin_event_recorder",
    )
    yield workflow
    e2e_client.delete(
        "/api/files/editor?path=e2e_builtin_event_recorder.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def failing_workflow(e2e_client, platform_admin):
    workflow = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_builtin_event_fails.py",
        FAILING_WORKFLOW,
        "e2e_builtin_event_fails",
    )
    yield workflow
    e2e_client.delete(
        "/api/files/editor?path=e2e_builtin_event_fails.py",
        headers=platform_admin.headers,
    )


def _create_topic_source(e2e_client, headers, topic: str, workflow_id: str) -> dict:
    response = e2e_client.post(
        "/api/events/sources",
        headers=headers,
        json={
            "name": f"E2E {topic} {uuid4().hex[:8]}",
            "source_type": "topic",
            "event_type": topic,
        },
    )
    assert response.status_code == 201, response.text
    source = response.json()

    sub_resp = e2e_client.post(
        f"/api/events/sources/{source['id']}/subscriptions",
        headers=headers,
        json={"workflow_id": workflow_id, "event_type": topic},
    )
    assert sub_resp.status_code == 201, sub_resp.text
    return source


def _delete_source(e2e_client, headers, source: dict) -> None:
    e2e_client.delete(f"/api/events/sources/{source['id']}", headers=headers)


def _wait_for_successful_delivery(e2e_client, headers, source: dict, predicate):
    def find_delivery():
        events_resp = e2e_client.get(
            f"/api/events/sources/{source['id']}/events",
            headers=headers,
        )
        if events_resp.status_code != 200:
            return None

        for event in events_resp.json()["items"]:
            if not predicate(event):
                continue
            deliveries_resp = e2e_client.get(
                f"/api/events/{event['id']}/deliveries",
                headers=headers,
            )
            if deliveries_resp.status_code != 200:
                return None
            deliveries = deliveries_resp.json()["items"]
            successful = [d for d in deliveries if d["status"] == "success"]
            if successful:
                return {"event": event, "delivery": successful[0]}
        return None

    result = poll_until(find_delivery, max_wait=20.0, interval=0.5)
    assert result is not None, f"No successful delivery for {source['event_type']}"
    return result


def _wait_for_failed_delivery(e2e_client, headers, source: dict, predicate):
    def find_delivery():
        events_resp = e2e_client.get(
            f"/api/events/sources/{source['id']}/events",
            headers=headers,
        )
        if events_resp.status_code != 200:
            return None

        for event in events_resp.json()["items"]:
            if not predicate(event):
                continue
            deliveries_resp = e2e_client.get(
                f"/api/events/{event['id']}/deliveries",
                headers=headers,
            )
            if deliveries_resp.status_code != 200:
                return None
            deliveries = deliveries_resp.json()["items"]
            failed = [d for d in deliveries if d["status"] == "failed"]
            if failed:
                return {"event": event, "delivery": failed[0]}
        return None

    result = poll_until(find_delivery, max_wait=20.0, interval=0.5)
    assert result is not None, f"No failed delivery for {source['event_type']}"
    return result


def _admin_principal(platform_admin) -> UserPrincipal:
    return UserPrincipal(
        user_id=platform_admin.user_id,
        email=platform_admin.email,
        organization_id=platform_admin.organization_id,
        name=platform_admin.name,
        is_superuser=True,
        is_verified=True,
    )


@pytest.mark.e2e
class TestBuiltInWorkflowEvents:
    def test_workflow_failure_and_retry_exhausted_events_run_subscribers(
        self, e2e_client, platform_admin, recorder_workflow, failing_workflow
    ):
        failed_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "workflow.failed",
            recorder_workflow["id"],
        )
        exhausted_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "workflow.retry_exhausted",
            recorder_workflow["id"],
        )
        try:
            execution = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                failing_workflow["id"],
                max_wait=30.0,
            )
            assert execution["status"] == "Failed"
            execution_id = execution["execution_id"]

            failed = _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                failed_source,
                lambda event: event["data"]["execution"]["id"] == execution_id,
            )
            exhausted = _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                exhausted_source,
                lambda event: event["data"]["execution"]["id"] == execution_id,
            )

            assert failed["event"]["data"]["workflow"]["id"] == failing_workflow["id"]
            assert exhausted["event"]["data"]["error"]["retryable"] is False
        finally:
            _delete_source(e2e_client, platform_admin.headers, failed_source)
            _delete_source(e2e_client, platform_admin.headers, exhausted_source)

    def test_event_delivery_retry_exhausted_event_runs_subscriber(
        self, e2e_client, platform_admin, recorder_workflow, failing_workflow
    ):
        delivery_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "event.delivery_retry_exhausted",
            recorder_workflow["id"],
        )
        trigger_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "test.delivery_failure",
            failing_workflow["id"],
        )
        try:
            emit_resp = e2e_client.post(
                "/api/events/emit",
                headers=platform_admin.headers,
                json={
                    "topic": "test.delivery_failure",
                    "data": {"marker": "delivery-failure"},
                },
            )
            assert emit_resp.status_code == 200, emit_resp.text
            emitted_id = emit_resp.json()["event_id"]

            _wait_for_failed_delivery(
                e2e_client,
                platform_admin.headers,
                trigger_source,
                lambda event: event["id"] == emitted_id,
            )

            result = _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                delivery_source,
                lambda event: event["data"]["event"]["id"] == emitted_id,
            )
            assert result["event"]["data"]["event"]["type"] == "test.delivery_failure"
            assert result["event"]["data"]["delivery"]["target_id"] == failing_workflow["id"]
        finally:
            _delete_source(e2e_client, platform_admin.headers, trigger_source)
            _delete_source(e2e_client, platform_admin.headers, delivery_source)


@pytest.mark.e2e
class TestBuiltInIntegrationEvents:
    @pytest.mark.asyncio
    async def test_integration_events_run_subscribers(
        self, e2e_client, platform_admin, db_session, org1, recorder_workflow, monkeypatch
    ):
        connected_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "integration.connected",
            recorder_workflow["id"],
        )
        disconnected_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "integration.disconnected",
            recorder_workflow["id"],
        )
        failed_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "integration.refresh_failed",
            recorder_workflow["id"],
        )
        reauth_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "integration.reauth_required",
            recorder_workflow["id"],
        )
        recovered_source = _create_topic_source(
            e2e_client,
            platform_admin.headers,
            "integration.refresh_recovered",
            recorder_workflow["id"],
        )
        integration_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": f"e2e_builtin_connect_{uuid4().hex[:8]}"},
        )
        assert integration_resp.status_code == 201, integration_resp.text
        integration = integration_resp.json()
        integration_id = UUID(integration["id"])

        provider = OAuthProvider(
            provider_name=f"builtin_provider_{uuid4().hex[:8]}",
            display_name=integration["name"],
            oauth_flow_type="authorization_code",
            client_id="client",
            encrypted_client_secret=encrypt_secret("secret").encode(),
            authorization_url="https://login.example.test/authorize",
            token_url="https://login.example.test/token",
            scopes=["read"],
            integration_id=integration_id,
        )
        mapping = IntegrationMapping(
            integration_id=integration_id,
            organization_id=UUID(org1["id"]),
            entity_id="tenant-123",
            entity_name="Tenant 123",
        )
        token = OAuthToken(
            provider=provider,
            organization_id=UUID(org1["id"]),
            encrypted_access_token=encrypt_secret("access").encode(),
            encrypted_refresh_token=encrypt_secret("refresh").encode(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["read"],
            status="completed",
        )
        db_session.add_all([provider, mapping, token])
        await db_session.commit()
        await db_session.refresh(provider)
        await db_session.refresh(mapping)
        await db_session.refresh(token)

        try:
            from src.routers.oauth_connections import _apply_callback_to_mapping
            from src.routers import integrations
            from src.services import oauth_provider

            await _apply_callback_to_mapping(
                db_session,
                mapping.id,
                token,
                provider,
                callback_url_params={},
                token_response={},
            )
            await db_session.commit()

            connected = _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                connected_source,
                lambda event: event["data"]["connection"]["id"] == str(mapping.id),
            )
            assert connected["event"]["data"]["organization"]["id"] == org1["id"]

            async def fail_refresh(_td):
                return {
                    "token_id": token.id,
                    "provider_id": provider.id,
                    "success": False,
                    "error": "Token refresh failed: invalid_grant",
                }

            monkeypatch.setattr(
                oauth_provider,
                "refresh_oauth_token_http",
                fail_refresh,
            )
            ctx = ExecutionContext(
                user=_admin_principal(platform_admin),
                org_id=platform_admin.organization_id,
                db=db_session,
            )
            with pytest.raises(HTTPException) as exc_info:
                await integrations.refresh_mapping_oauth(
                    integration_id,
                    mapping.id,
                    ctx,
                    _admin_principal(platform_admin),
                )
            assert exc_info.value.status_code == 502
            await db_session.commit()

            _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                failed_source,
                lambda event: event["data"]["connection"]["id"] == str(mapping.id),
            )
            _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                reauth_source,
                lambda event: event["data"]["connection"]["id"] == str(mapping.id),
            )

            async def recover_refresh(_td):
                return {
                    "token_id": token.id,
                    "provider_id": provider.id,
                    "success": True,
                    "access_token": "new-access",
                    "encrypted_access_token": encrypt_secret("new-access").encode(),
                    "encrypted_refresh_token": encrypt_secret("refresh").encode(),
                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                    "scopes": ["read"],
                }

            monkeypatch.setattr(
                oauth_provider,
                "refresh_oauth_token_http",
                recover_refresh,
            )
            await integrations.refresh_mapping_oauth(
                integration_id,
                mapping.id,
                ctx,
                _admin_principal(platform_admin),
            )
            await db_session.commit()

            recovered = _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                recovered_source,
                lambda event: event["data"]["connection"]["id"] == str(mapping.id),
            )
            assert recovered["event"]["data"]["refresh"]["last_success_at"] is not None

            disconnect_resp = e2e_client.post(
                f"/api/integrations/{integration_id}/mappings/{mapping.id}/oauth/disconnect",
                headers=platform_admin.headers,
            )
            assert disconnect_resp.status_code == 204, disconnect_resp.text

            disconnected = _wait_for_successful_delivery(
                e2e_client,
                platform_admin.headers,
                disconnected_source,
                lambda event: event["data"]["connection"]["id"] == str(mapping.id),
            )
            assert disconnected["event"]["data"]["integration"]["id"] == str(integration_id)
        finally:
            _delete_source(e2e_client, platform_admin.headers, connected_source)
            _delete_source(e2e_client, platform_admin.headers, disconnected_source)
            _delete_source(e2e_client, platform_admin.headers, failed_source)
            _delete_source(e2e_client, platform_admin.headers, reauth_source)
            _delete_source(e2e_client, platform_admin.headers, recovered_source)
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )
