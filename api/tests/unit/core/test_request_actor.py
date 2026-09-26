"""Unit tests for the shared audit-actor builder.

Covers every branch of ``actor_from_token_payload`` — no payload, human
access token, service token, engine token with and without a signed caller —
including malformed UUID claims (which must never raise).
"""

from uuid import uuid4

from src.core.constants import SYSTEM_USER_ID, SYSTEM_USER_UUID
from src.core.request_actor import actor_from_token_payload


def _actor(payload):
    return actor_from_token_payload(
        payload, ip_address="10.0.0.1", user_agent="pytest"
    )


class TestPayloadAbsent:
    def test_none_payload_returns_none(self):
        assert _actor(None) is None


class TestHumanToken:
    def test_sub_org_and_identity_map_to_http_actor(self):
        user_id = uuid4()
        org_id = uuid4()
        actor = _actor(
            {
                "sub": str(user_id),
                "org_id": str(org_id),
                "email": "user@example.com",
                "name": "User Example",
            }
        )
        assert actor is not None
        assert actor.user_id == user_id
        assert actor.organization_id == org_id
        assert actor.email == "user@example.com"
        assert actor.name == "User Example"
        assert actor.source == "http"
        assert actor.execution_id is None
        assert actor.ip_address == "10.0.0.1"
        assert actor.user_agent == "pytest"

    def test_invalid_uuid_claims_become_none(self):
        actor = _actor(
            {
                "sub": "not-a-uuid",
                "org_id": "also-not-a-uuid",
                "email": "user@example.com",
            }
        )
        assert actor is not None
        assert actor.user_id is None
        assert actor.organization_id is None
        assert actor.source == "http"
        assert actor.execution_id is None


class TestServiceToken:
    def test_service_claim_uses_sentinel_and_service_source(self):
        org_id = uuid4()
        execution_id = uuid4()
        actor = _actor(
            {
                "sub": SYSTEM_USER_ID,
                "service_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "org_id": str(org_id),
                "engine_execution_id": str(execution_id),
                "email": "service-aaaaaaaaaaaa@bifrost.internal",
                "name": "service-aaaaaaaaaaaa",
            }
        )
        assert actor is not None
        assert actor.user_id == SYSTEM_USER_UUID
        assert actor.organization_id == org_id
        assert actor.email == "service-aaaaaaaaaaaa@bifrost.internal"
        assert actor.name == "service-aaaaaaaaaaaa"
        assert actor.source == "service"
        assert actor.execution_id == execution_id

    def test_malformed_execution_id_is_none(self):
        actor = _actor(
            {
                "service_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "engine_execution_id": "attempt-1",
            }
        )
        assert actor is not None
        assert actor.source == "service"
        assert actor.execution_id is None


class TestEngineTokenWithCaller:
    def test_caller_claims_win_and_source_is_workflow(self):
        caller_id = uuid4()
        org_id = uuid4()
        execution_id = uuid4()
        actor = _actor(
            {
                "sub": SYSTEM_USER_ID,
                "engine_execution_id": str(execution_id),
                "engine_caller_user_id": str(caller_id),
                "engine_caller_org_id": str(org_id),
                "engine_caller_email": "caller@example.com",
                "engine_caller_name": "Caller Name",
                "email": "engine@bifrost.internal",
                "name": "Bifrost Engine",
            }
        )
        assert actor is not None
        assert actor.user_id == caller_id
        assert actor.organization_id == org_id
        assert actor.email == "caller@example.com"
        assert actor.name == "Caller Name"
        assert actor.source == "workflow"
        assert actor.execution_id == execution_id

    def test_malformed_caller_uuid_is_none_not_raised(self):
        actor = _actor(
            {
                "engine_execution_id": str(uuid4()),
                "engine_caller_user_id": "not-a-uuid",
                "engine_caller_org_id": "not-a-uuid-either",
                "engine_caller_email": "caller@example.com",
                "engine_caller_name": "Caller Name",
            }
        )
        assert actor is not None
        assert actor.user_id is None
        assert actor.organization_id is None
        assert actor.source == "workflow"


class TestEngineTokenWithoutHumanCaller:
    def test_absent_caller_uses_sentinel_with_workflow_source(self):
        org_id = uuid4()
        execution_id = uuid4()
        actor = _actor(
            {
                "sub": SYSTEM_USER_ID,
                "engine_execution_id": str(execution_id),
                "engine_caller_org_id": str(org_id),
                "email": "engine@bifrost.internal",
                "name": "Bifrost Engine",
            }
        )
        assert actor is not None
        assert actor.user_id == SYSTEM_USER_UUID
        assert actor.organization_id == org_id
        assert actor.email == "engine@bifrost.internal"
        assert actor.name == "Bifrost Engine"
        assert actor.source == "workflow"
        assert actor.execution_id == execution_id

    def test_sentinel_caller_is_not_treated_as_human(self):
        actor = _actor(
            {
                "engine_execution_id": str(uuid4()),
                "engine_caller_user_id": SYSTEM_USER_ID,
                "email": "engine@bifrost.internal",
                "name": "Bifrost Engine",
            }
        )
        assert actor is not None
        assert actor.user_id == SYSTEM_USER_UUID
        assert actor.organization_id is None
        assert actor.source == "workflow"

    def test_malformed_execution_id_is_none(self):
        actor = _actor(
            {
                "engine_execution_id": "not-a-uuid",
                "email": "engine@bifrost.internal",
            }
        )
        assert actor is not None
        assert actor.execution_id is None
        assert actor.source == "workflow"
