"""Unit tests for ``mint_engine_token`` caller attribution claims.

The ``engine_caller_*`` claims are audit attribution only: they must be
emitted when supplied, omitted when ``None``, and never alter ``sub`` or
``is_superuser``.
"""

from uuid import uuid4

from src.core.security import decode_token, mint_engine_token


def _decode(token: str) -> dict:
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    return payload


class TestMintEngineTokenCallerClaims:
    def test_omits_caller_claims_when_none(self):
        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
        )
        payload = _decode(token)
        for claim in (
            "engine_caller_user_id",
            "engine_caller_org_id",
            "engine_caller_email",
            "engine_caller_name",
        ):
            assert claim not in payload

    def test_emits_caller_claims_when_supplied(self):
        caller_id = uuid4()
        org_id = uuid4()
        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            caller_user_id=str(caller_id),
            caller_organization_id=str(org_id),
            caller_email="caller@example.com",
            caller_name="Caller Name",
        )
        payload = _decode(token)
        assert payload["engine_caller_user_id"] == str(caller_id)
        assert payload["engine_caller_org_id"] == str(org_id)
        assert payload["engine_caller_email"] == "caller@example.com"
        assert payload["engine_caller_name"] == "Caller Name"

    def test_sub_and_superuser_unchanged_by_caller_claims(self):
        before, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=False,
            timeout_seconds=60,
        )
        after, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=False,
            timeout_seconds=60,
            caller_user_id=str(uuid4()),
            caller_organization_id=str(uuid4()),
            caller_email="caller@example.com",
            caller_name="Caller Name",
        )
        before_payload = _decode(before)
        after_payload = _decode(after)
        assert after_payload["sub"] == before_payload["sub"]
        assert after_payload["is_superuser"] is True
        assert before_payload["is_superuser"] is True


class TestMintEngineTokenWorkflowIdClaim:
    """``engine_workflow_id`` (R2a-2) is likewise audit attribution only."""

    def test_omitted_when_none(self):
        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
        )
        assert "engine_workflow_id" not in _decode(token)

    def test_emitted_when_supplied(self):
        workflow_id = str(uuid4())
        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            engine_workflow_id=workflow_id,
        )
        assert _decode(token)["engine_workflow_id"] == workflow_id

    def test_sub_and_superuser_unchanged(self):
        before, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=False,
            timeout_seconds=60,
        )
        after, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=False,
            timeout_seconds=60,
            engine_workflow_id=str(uuid4()),
        )
        before_payload = _decode(before)
        after_payload = _decode(after)
        assert after_payload["sub"] == before_payload["sub"]
        assert after_payload["is_superuser"] is True


class TestMintEngineTokenLineageClaims:
    """``engine_run_user_id`` etc. are attribution only, like ``engine_caller_*``."""

    def test_emits_lineage_claims_when_supplied(self):
        run_user, root = str(uuid4()), str(uuid4())
        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            lineage={"run_user_id": run_user, "started_by_user_id": run_user, "root_execution_id": root},
        )
        payload = _decode(token)
        assert payload["engine_run_user_id"] == run_user
        assert payload["engine_started_by_user_id"] == run_user
        assert payload["engine_root_execution_id"] == root
        assert payload["is_superuser"] is True

    def test_omits_lineage_claims_when_none(self):
        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            lineage=None,
        )
        payload = _decode(token)
        for claim in ("engine_run_user_id", "engine_started_by_user_id", "engine_root_execution_id"):
            assert claim not in payload
