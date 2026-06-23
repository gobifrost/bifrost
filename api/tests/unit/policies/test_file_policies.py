"""Policy evaluation for file metadata uses the shared policy engine."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.file_policies import (
    FilePolicyContext,
    evaluate_file_action,
    make_seed_admin_bypass,
    select_longest_prefix,
)
from src.models.contracts.policies import FilePolicies


def _policies(*rules: dict) -> FilePolicies:
    return FilePolicies.model_validate({"policies": list(rules)})


def _user(**overrides):
    base = {
        "user_id": str(uuid4()),
        "email": "u@example.com",
        "organization_id": str(uuid4()),
        "is_platform_admin": False,
        "role_ids": [],
        "role_names": [],
        "claims": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _ctx(**overrides) -> FilePolicyContext:
    base = {
        "location": "workspace",
        "path": "reports/q1.pdf",
        "created_by": "creator@example.com",
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return FilePolicyContext(**base)


def test_file_policy_contract_accepts_file_actions() -> None:
    policies = _policies(
        {"name": "reader", "actions": ["read"], "when": None},
        {"name": "writer", "actions": ["write"], "when": None},
        {"name": "deleter", "actions": ["delete"], "when": None},
        {"name": "lister", "actions": ["list"], "when": None},
    )

    assert [rule.actions[0] for rule in policies.policies] == [
        "read",
        "write",
        "delete",
        "list",
    ]


def test_file_policy_contract_rejects_invalid_action() -> None:
    with pytest.raises(ValidationError, match="execute"):
        _policies({"name": "bad", "actions": ["execute"], "when": None})


def test_file_namespace_resolves_location_path_creator_and_created_at() -> None:
    ctx = _ctx(created_by="owner@example.com")
    policies = _policies({
        "name": "matching_file",
        "actions": ["read"],
        "when": {
            "and": [
                {"eq": [{"file": "location"}, "workspace"]},
                {"eq": [{"file": "path"}, "reports/q1.pdf"]},
                {"eq": [{"file": "created_by"}, "owner@example.com"]},
                {"not": {"is_null": {"file": "created_at"}}},
            ]
        },
    })

    assert evaluate_file_action("read", policies, ctx, _user()) is True


def test_unknown_file_namespace_field_fails_closed() -> None:
    policies = _policies({
        "name": "unknown_file_ref",
        "actions": ["read"],
        "when": {"eq": [{"file": "missing_field"}, "anything"]},
    })

    assert evaluate_file_action("read", policies, _ctx(), _user()) is False


def test_default_deny_when_no_rule_matches_action() -> None:
    policies = _policies({"name": "read_only", "actions": ["read"], "when": None})

    assert evaluate_file_action("delete", policies, _ctx(), _user()) is False


def test_seed_admin_bypass_allows_admin_only() -> None:
    policies = FilePolicies.model_validate(make_seed_admin_bypass())

    assert evaluate_file_action(
        "write", policies, _ctx(), _user(is_platform_admin=True)
    ) is True
    assert evaluate_file_action(
        "write", policies, _ctx(), _user(is_platform_admin=False)
    ) is False


def test_has_role_function_is_shared_with_table_policies() -> None:
    policies = _policies({
        "name": "ops_write",
        "actions": ["write"],
        "when": {"call": "has_role", "args": ["ops"]},
    })

    assert (
        evaluate_file_action("write", policies, _ctx(), _user(role_names=["ops"]))
        is True
    )
    assert (
        evaluate_file_action("write", policies, _ctx(), _user(role_names=["viewer"]))
        is False
    )


def test_creator_only_policy_uses_file_created_by() -> None:
    policies = _policies({
        "name": "creator_delete",
        "actions": ["delete"],
        "when": {"eq": [{"file": "created_by"}, {"user": "user_id"}]},
    })
    creator_id = str(uuid4())

    assert evaluate_file_action(
        "delete", policies, _ctx(created_by=creator_id), _user(user_id=creator_id)
    ) is True
    assert evaluate_file_action(
        "delete", policies, _ctx(created_by=str(uuid4())), _user(user_id=creator_id)
    ) is False


def test_custom_claims_are_resolved_from_user_claims() -> None:
    policies = _policies({
        "name": "campus_read",
        "actions": ["read"],
        "when": {"in": [{"file": "path"}, {"claims": "allowed_file_paths"}]},
    })

    assert evaluate_file_action(
        "read",
        policies,
        _ctx(path="reports/q1.pdf"),
        _user(claims={"allowed_file_paths": ["reports/q1.pdf"]}),
    ) is True
    assert evaluate_file_action(
        "read",
        policies,
        _ctx(path="reports/q2.pdf"),
        _user(claims={"allowed_file_paths": ["reports/q1.pdf"]}),
    ) is False


def test_select_longest_prefix_uses_path_boundaries() -> None:
    root = SimpleNamespace(location="workspace", path="")
    reports = SimpleNamespace(location="workspace", path="reports")
    q1 = SimpleNamespace(location="workspace", path="reports/q1")
    sibling = SimpleNamespace(location="workspace", path="reports2")

    assert (
        select_longest_prefix(
            [root, reports, q1, sibling], "workspace", "reports/q1/a.pdf"
        )
        is q1
    )
    assert (
        select_longest_prefix(
            [root, reports, q1, sibling], "workspace", "reports2/a.pdf"
        )
        is sibling
    )
    assert select_longest_prefix([reports], "workspace", "reports2/a.pdf") is None
