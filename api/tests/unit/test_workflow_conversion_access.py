from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from shared.access_checks import ALL_ORGS
from shared.workflow_conversion_access import (
    new_identity_requirements,
    recorded_identity_requirements,
    recorded_permission_requirements,
    source_identity_requirements,
)
from src.services.recommended_access import CheckedAction, _recorded_actions


HOME = UUID("00000000-0000-0000-0000-000000000011")
OTHER = UUID("00000000-0000-0000-0000-000000000022")


def test_unobserved_source_scope_is_still_a_dedicated_identity_requirement():
    analysis = {
        "sdk_calls": ["bifrost.config.get"],
        "resource_calls": [{
            "call": "bifrost.config.get", "path": "workflows/example.py", "line": 3,
            "selectors": {"scope": str(OTHER)}, "dynamic_selectors": [],
        }],
    }

    result = source_identity_requirements(analysis, HOME)

    assert len(result) == 1
    assert str(OTHER) in result[0]
    assert source_identity_requirements(analysis, OTHER) == []


def test_dynamic_source_scope_is_not_inferred_from_incomplete_observations():
    result = source_identity_requirements({
        "sdk_calls": ["bifrost.config.get"],
        "resource_calls": [{
            "call": "bifrost.config.get", "path": "workflows/example.py", "line": 3,
            "selectors": {}, "dynamic_selectors": ["scope"],
        }],
    }, HOME)

    assert len(result) == 1
    assert "organization reach is determined at runtime" in result[0]


@pytest.mark.parametrize("call,allowed", [("bifrost.events.emit", True), ("bifrost.config.get", False)])
def test_source_scope_matches_each_sdk_operations_canonical_grammar(call, allowed):
    result = source_identity_requirements({
        "sdk_calls": [call],
        "resource_calls": [{
            "call": call, "path": "workflows/example.py", "line": 3,
            "selectors": {"scope": "GLOBAL"}, "dynamic_selectors": [],
        }],
    }, HOME)

    assert (result == []) is allowed


def test_a_scope_override_without_selector_facts_is_not_a_complete_reach_proposal():
    result = source_identity_requirements({
        "sdk_calls": ["bifrost.ExecutionContext.set_scope", "bifrost.config.get"],
        "resource_calls": [],
    }, HOME)

    assert len(result) == 1
    assert "scope override target is unavailable" in result[0]


def test_a_new_identity_does_not_inherit_observed_foreign_or_all_org_reach() -> None:
    result = new_identity_requirements([
        CheckedAction("scope_switch", HOME, ()),
        CheckedAction("scope_switch", OTHER, ()),
        CheckedAction("child_run", ALL_ORGS, ()),
    ], HOME)
    assert len(result) == 2
    assert any(str(OTHER) in note for note in result)
    assert any("all organizations" in note for note in result)


def test_policy_references_are_not_proof_that_an_identity_needs_every_role_or_claim() -> None:
    result = new_identity_requirements([
        CheckedAction("policy", HOME, ("role:Operators", "claim:region")),
        CheckedAction("policy", HOME, ("role:Operators",)),
    ], HOME)
    assert result == []


@pytest.mark.asyncio
async def test_dedicated_identity_requirements_do_not_include_human_started_runs(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared import run_lineage
    from src.models.orm.audit import AuditLog
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User

    organization = Organization(name="Contoso", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    identity = User(email=f"conversion-{uuid4()}@contoso.test", identity_kind="custom")
    person = User(
        email=f"reviewer-{uuid4()}@contoso.test", organization_id=organization.id
    )
    db_session.add_all([identity, person])
    await db_session.flush()
    workflow_id = uuid4()
    monkeypatch.setattr(
        run_lineage,
        "unattended_lineage",
        AsyncMock(return_value=SimpleNamespace(run_user_id=identity.id)),
    )
    for user, target in ((identity, OTHER), (person, HOME)):
        db_session.add(AuditLog(
            action="access.check",
            user_id=user.id,
            resource_type="scope_switch",
            execution_id=uuid4(),
            details={
                "workflow_id": str(workflow_id),
                "inputs": {"target": str(target), "missing": []},
            },
        ))
    await db_session.flush()

    result = await recorded_identity_requirements(
        db_session,
        SimpleNamespace(id=workflow_id, organization_id=None),
        window_days=30,
    )

    assert len(result) == 1
    assert str(OTHER) in result[0]
    all_actions, runs = await _recorded_actions(
        db_session, workflow_id, datetime.now(timezone.utc) - timedelta(days=30)
    )
    assert len(all_actions) == runs == 2


@pytest.mark.asyncio
async def test_observed_permissions_follow_successful_decisions_and_not_route_defaults(
    db_session,
) -> None:
    from src.models.orm.audit import AuditLog

    workflow_id = uuid4()

    def step(key, permission, status="passed"):
        return {"key": key, "status": status, "facts": {"permission": permission}}

    for outcome, steps in [
        ("success", [
            step("permission", "executions.read.all"),
            step("privileged_target", "privilegedaccess.readwrite"),
            step("secret", "secrets.read", "not_reached"),
            step("permission", "*"),
        ]),
        ("failure", [step("permission", "users.impersonate")]),
    ]:
        db_session.add(AuditLog(
            action="access.check",
            outcome=outcome,
            details={"workflow_id": str(workflow_id), "trace": {"steps": steps}},
        ))
    await db_session.flush()

    assert await recorded_permission_requirements(
        db_session, workflow_id, window_days=30
    ) == ["executions.read.all", "privilegedaccess.readwrite"]
