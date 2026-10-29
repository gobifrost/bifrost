"""Compact storage for access-check traces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from shared.access_trace_storage import (
    ACCESS_TRACE_STORAGE_VERSION,
    compact_access_check_details,
    expand_access_check_details,
)
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import RunUser, check_permission


def _details() -> dict[str, Any]:
    return {
        "enforced": True,
        "direct": False,
        "agent_run_id": "agent-run-1",
        "workflow_id": "workflow-1",
        "today": "allowed",
        "proposed_attribution": {"actor_user_id": "user-1", "organization_id": "org-1"},
        "inputs": {
            "operation": "GET /api/agents",
            "target": "org-1",
            "permission": "agents.read",
            "enforced": True,
        },
        "trace": {
            "outcome": "failure",
            "enforced": True,
            "steps": [
                {
                    "key": "run_user",
                    "label": "Run user",
                    "status": "passed",
                    "reason": "person",
                    "facts": {"user_id": "user-1", "home_organization_id": "org-1"},
                },
                {
                    "key": "permission",
                    "label": "Custom permission label",
                    "status": "stopped",
                    "reason": "missing",
                    "facts": {"permission": "agents.read"},
                },
                {
                    "key": "target",
                    "label": "Target in reach",
                    "status": "not_reached",
                    "reason": "",
                    "facts": {},
                },
            ],
        },
    }


def test_compact_trace_round_trips_stopped_and_unreached_steps() -> None:
    original = _details()

    compact = compact_access_check_details(original)
    expanded = expand_access_check_details(
        compact,
        outcome="failure",
        operation_id="GET /api/agents",
    )

    assert compact["access_trace_storage_version"] == ACCESS_TRACE_STORAGE_VERSION
    assert compact["trace"] == {
        "steps": [
            {
                "key": "run_user",
                "status": "passed",
                "reason": "person",
                "facts": {"user_id": "user-1", "home_organization_id": "org-1"},
            },
            {
                "key": "permission",
                "status": "stopped",
                "reason": "missing",
                "facts": {"permission": "agents.read"},
            },
        ],
        "order": ["run_user", {"key": "permission", "label": "Custom permission label"}, "target"],
    }
    assert "today" not in compact and "proposed_attribution" not in compact
    assert compact["inputs"] == {"target": "org-1", "permission": "agents.read", "enforced": True}
    assert expanded == {
        **{key: value for key, value in original.items() if key not in {"today", "proposed_attribution"}},
        "inputs": original["inputs"],
    }


def test_expansion_uses_the_audit_outcome_and_stored_enforcement() -> None:
    compact = compact_access_check_details(_details())

    expanded = expand_access_check_details(compact, outcome="success", operation_id="GET /api/agents")

    assert expanded["trace"] == {
        "outcome": "success",
        "enforced": True,
        "steps": _details()["trace"]["steps"],
    }


def test_legacy_details_are_not_reinterpreted() -> None:
    legacy = _details()

    expanded = expand_access_check_details(legacy, outcome="success", operation_id="ignored")

    assert expanded == legacy
    assert expanded is not legacy


@pytest.mark.parametrize(
    "details",
    [
        {"access_trace_storage_version": None},
        {"access_trace_storage_version": 2},
        {"access_trace_storage_version": 1, "enforced": False, "direct": False, "inputs": {}, "trace": {}},
    ],
)
def test_invalid_versioned_details_fail_explicitly(details: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        expand_access_check_details(details, outcome="failure", operation_id="GET /api/agents")


def test_compaction_does_not_mutate_the_writer_details() -> None:
    details = _details()
    before = deepcopy(details)

    compact_access_check_details(details)

    assert details == before


def test_compact_trace_retains_named_permission_after_an_earlier_stop() -> None:
    home = UUID("00000000-0000-0000-0000-00000000c001")
    run_user_id = uuid4()
    trace = check_permission(
        RunUser(
            user_id=run_user_id,
            ctx=AuthorizationContext(
                user_id=run_user_id,
                home_organization_id=home,
                base_role_id=USER_ROLE_ID,
                is_external=False,
                base_permissions=USER_BASE_PERMISSIONS,
                role_grants=(),
            ),
            identity_kind=None,
            is_active=True,
        ),
        None,
        "agents.read",
        UUID("00000000-0000-0000-0000-00000000f001"),
    )
    details = {
        "enforced": False,
        "direct": True,
        "workflow_id": None,
        "inputs": {"operation": "GET /api/agents", "target": "org-1", "permission": "agents.read"},
        "trace": trace.as_dict(),
    }

    compact = compact_access_check_details(details)
    expanded = expand_access_check_details(compact, outcome=trace.outcome, operation_id="GET /api/agents")

    assert compact["trace"]["steps"][0]["key"] == "run_user"
    assert compact["trace"]["steps"][-1] == {
        "key": "permission",
        "status": "not_reached",
        "reason": "",
        "facts": {"permission": "agents.read", "permission_display_name": "Read Agents"},
    }
    assert expanded["trace"] == trace.as_dict()
