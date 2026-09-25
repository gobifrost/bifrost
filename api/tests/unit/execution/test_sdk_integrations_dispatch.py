"""Stage 2b: parent dispatcher unit tests for the integrations reads.

Covers solution-id derivation (parent-owned, fail-closed), the
integrations allowlist boundary, DTO validation parity with HTTP, the
child-solution forgery rule, and declared-Solution 424 mapping. Data
parity itself lives in ``tests/unit/sdk/test_sdk_integrations_local.py``.
"""

import contextlib
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.execution.sdk_local_dispatch import (
    LocalDispatchPrincipal,
    dispatch_frame,
    principal_from_context,
)


@contextlib.asynccontextmanager
async def _factory(session):
    yield session


def _principal(**kwargs):
    kwargs.setdefault("actor_email", "dispatch-actor@test.local")
    return LocalDispatchPrincipal(
        caller_org_id=kwargs.get("org_id"),
        is_platform_admin=kwargs.get("is_platform_admin", False),
        is_provider_org=kwargs.get("is_provider_org", False),
        is_external=kwargs.get("is_external", False),
        actor_email=kwargs.get("actor_email"),
        solution_id=kwargs.get("solution_id"),
    )


class TestSolutionDerivation:
    def test_missing_solution_means_loose(self):
        principal = principal_from_context({"organization": None})
        assert principal.solution_id is None

    def test_empty_solution_means_loose(self):
        principal = principal_from_context(
            {"organization": None, "solution_id": ""}
        )
        assert principal.solution_id is None

    def test_valid_solution_derived(self):
        sid = uuid4()
        principal = principal_from_context(
            {"organization": None, "solution_id": str(sid)}
        )
        assert principal.solution_id == sid

    def test_malformed_solution_fails_closed(self):
        from src.services.execution.sdk_local_dispatch import LocalPrincipalError

        for bad in ("junk", 12345, ["x"]):
            with pytest.raises(LocalPrincipalError, match="solution id"):
                principal_from_context(
                    {"organization": None, "solution_id": bad}
                )

    def test_service_context_carries_solution(self):
        sid = uuid4()
        service_id = str(uuid4())
        principal = principal_from_context(
            {
                "organization": None,
                "solution_id": str(sid),
                "service": {"service_id": service_id, "attempt_id": str(uuid4())},
            }
        )
        assert principal.solution_id == sid
        assert principal.is_service is True

    def test_child_claims_cannot_set_solution(self):
        # The principal never reads frame-shaped keys, even if a hostile
        # child smuggles them into the context mapping.
        sid = uuid4()
        principal = principal_from_context(
            {
                "organization": None,
                "solution": str(sid),
                "solution_id": None,
            }
        )
        assert principal.solution_id is None


@pytest.mark.asyncio
class TestIntegrationsDispatchValidation:
    async def _dispatch(self, db_session, frame, principal):
        return await dispatch_frame(
            lambda: _factory(db_session), principal, frame
        )

    async def test_new_ops_are_allowlisted(self, db_session):
        from shared import sdk_integrations as shared_service

        principal = _principal(is_platform_admin=True)
        with (
            patch.object(
                shared_service,
                "get_sdk_integration_dict",
                new=AsyncMock(return_value={"integration_id": "iid"}),
            ),
            patch.object(
                shared_service,
                "list_sdk_integration_mappings",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                shared_service,
                "get_sdk_integration_mapping_dict",
                new=AsyncMock(return_value=None),
            ),
        ):
            get_resp = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "g-1", "op": "integrations.get",
                 "name": "P", "scope": "global", "oauth_scope": None},
            )
            list_resp = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "l-1", "op": "integrations.list_mappings",
                 "name": "P", "scope": "global"},
            )
            gm_resp = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "m-1", "op": "integrations.get_mapping",
                 "name": "P", "scope": "global", "entity_id": None},
            )
        assert get_resp == {"v": 1, "id": "g-1", "ok": True,
                            "result": {"integration_id": "iid"}}
        assert list_resp["ok"] is True
        assert list_resp["result"] == {"items": []}
        assert gm_resp == {"v": 1, "id": "m-1", "ok": True, "result": None}

    async def test_mutations_and_refresh_stay_http_only(self, db_session):
        principal = _principal(is_platform_admin=True)
        for op in (
            "integrations.upsert_mapping",
            "integrations.delete_mapping",
            "integrations.refresh_token",
        ):
            response = await self._dispatch(
                db_session,
                {"v": 1, "id": "x-1", "op": op, "name": "P", "scope": "global"},
                principal,
            )
            assert response["ok"] is False
            assert response["status"] == 404

    async def test_malformed_fields_are_422(self, db_session):
        principal = _principal(is_platform_admin=True)
        bad_frames = [
            {"v": 1, "id": "b-1", "op": "integrations.get",
             "name": {"nested": 1}, "scope": "global", "oauth_scope": None},
            {"v": 1, "id": "b-2", "op": "integrations.list_mappings",
             "name": 42, "scope": "global"},
            {"v": 1, "id": "b-3", "op": "integrations.get_mapping",
             "name": "P", "scope": "global", "entity_id": {"nested": 1}},
            {"v": 1, "id": "b-4", "op": "integrations.get",
             "name": "P", "scope": "not-a-uuid", "oauth_scope": None},
        ]
        for frame in bad_frames:
            response = await self._dispatch(db_session, frame, principal)
            assert response["ok"] is False, frame
            assert response["status"] == 422, frame

    async def test_cross_org_denied_without_bypass(self, db_session):
        from src.models.orm.integrations import Integration

        db_session.add(Integration(name="P"))
        await db_session.flush()
        principal = _principal(org_id=uuid4())
        for frame in (
            {"v": 1, "id": "c-1", "op": "integrations.get",
             "name": "P", "scope": str(uuid4()), "oauth_scope": None},
            {"v": 1, "id": "c-2", "op": "integrations.list_mappings",
             "name": "P", "scope": str(uuid4())},
            {"v": 1, "id": "c-3", "op": "integrations.get_mapping",
             "name": "P", "scope": str(uuid4()), "entity_id": None},
        ):
            response = await self._dispatch(db_session, frame, principal)
            assert response["ok"] is False
            assert response["status"] == 403

    async def test_missing_mapping_integration_returns_null_before_scope_check(self, db_session):
        principal = _principal(org_id=uuid4())
        for frame in (
            {"v": 1, "id": "m-1", "op": "integrations.list_mappings",
             "name": "missing", "scope": str(uuid4())},
            {"v": 1, "id": "m-2", "op": "integrations.get_mapping",
             "name": "missing", "scope": str(uuid4()), "entity_id": None},
        ):
            response = await self._dispatch(db_session, frame, principal)
            assert response["ok"] is True
            assert response["result"] is None

    async def test_child_solution_claim_is_ignored(self, db_session):
        """A forged frame solution never reaches the service."""
        from shared import sdk_integrations as shared_service

        forged = uuid4()
        owned = uuid4()
        principal = _principal(is_platform_admin=True, solution_id=owned)
        seen = {}

        async def _spy(session, **kwargs):
            seen.update(kwargs)
            return None

        with patch.object(shared_service, "get_sdk_integration_dict", new=_spy):
            response = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "s-1", "op": "integrations.get",
                 "name": "P", "scope": "global", "oauth_scope": None,
                 "solution": str(forged)},
            )
        assert response["ok"] is True
        assert seen["solution_id"] == owned
        assert seen["solution_id"] != forged

    async def test_declared_missing_maps_to_424(self, db_session):
        from shared import sdk_integrations as shared_service
        from shared.sdk_integrations import IntegrationServiceError

        principal = _principal(is_platform_admin=True)
        with patch.object(
            shared_service,
            "get_sdk_integration_dict",
            new=AsyncMock(
                side_effect=IntegrationServiceError(424, "Required integration 'P'")
            ),
        ):
            response = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "d-1", "op": "integrations.get",
                 "name": "P", "scope": "global", "oauth_scope": None},
            )
        assert response["ok"] is False
        assert response["status"] == 424
        assert "Required integration" in response["detail"]

    async def test_dispatcher_calls_shared_service(self, db_session):
        from shared import sdk_integrations as shared_service

        org_id = uuid4()
        solution_id = uuid4()
        principal = _principal(org_id=org_id, solution_id=solution_id)
        seen = {}

        async def _spy(session, **kwargs):
            seen.update(kwargs)
            return None

        with patch.object(shared_service, "get_sdk_integration_dict", new=_spy):
            response = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "p-1", "op": "integrations.get",
                 "name": "P", "scope": str(org_id),
                 "oauth_scope": "https://example.com/.default"},
            )
        assert response["ok"] is True
        assert seen == {
            "name": "P",
            "org_id": org_id,
            "oauth_scope": "https://example.com/.default",
            "solution_id": solution_id,
            "external": False,
        }

    async def test_one_short_session_per_operation(self, db_session):
        from shared import sdk_integrations as shared_service

        principal = _principal(is_platform_admin=True)
        opened = 0
        closed = 0

        @contextlib.asynccontextmanager
        async def _counting_factory():
            nonlocal opened, closed
            opened += 1
            try:
                yield db_session
            finally:
                closed += 1

        with (
            patch.object(
                shared_service,
                "get_sdk_integration_dict",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                shared_service,
                "list_sdk_integration_mappings",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                shared_service,
                "get_sdk_integration_mapping_dict",
                new=AsyncMock(return_value=None),
            ),
        ):
            frames = [
                {"v": 1, "id": "o-1", "op": "integrations.get",
                 "name": "P", "scope": "global", "oauth_scope": None},
                {"v": 1, "id": "o-2", "op": "integrations.list_mappings",
                 "name": "P", "scope": "global"},
                {"v": 1, "id": "o-3", "op": "integrations.get_mapping",
                 "name": "P", "scope": "global", "entity_id": None},
            ]
            for frame in frames:
                response = await dispatch_frame(
                    _counting_factory, principal, frame
                )
                assert response["ok"] is True, frame
        assert opened == 3
        assert closed == 3
