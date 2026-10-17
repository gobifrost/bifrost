"""Keep the historical seed valid while pinning the current authority seed."""
import json
from pathlib import Path

import pytest

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from shared.policies.probe import evaluate_action, make_seed_admin_bypass
from src.models.contracts.policies import TablePolicies


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260504_backfill_table_access_admin_bypass.py"
)


def _extract_seed_from_migration() -> dict:
    """Pull the SEED_ADMIN_BYPASS literal out of the migration file."""
    src = MIGRATION_PATH.read_text()
    marker = 'SEED_ADMIN_BYPASS = """'
    start = src.index(marker) + len(marker)
    end = src.index('"""', start)
    return json.loads(src[start:end])


def test_historical_migration_seed_remains_the_legacy_user_fact():
    migration_seed = _extract_seed_from_migration()
    assert migration_seed["policies"][0]["when"] == {"user": "is_platform_admin"}


def test_runtime_seed_uses_execution_authority():
    runtime_seed = make_seed_admin_bypass()
    assert runtime_seed["policies"][0]["when"] == {"authority": "platform_admin"}


def test_migration_seed_validates_as_table_policies():
    """The shape the migration writes must round-trip through Pydantic."""
    migration_seed = _extract_seed_from_migration()
    parsed = TablePolicies.model_validate(migration_seed)
    assert len(parsed.policies) == 1
    only = parsed.policies[0]
    assert only.name == "admin_bypass"
    assert set(only.actions) == {"read", "create", "update", "delete"}


@pytest.mark.parametrize(
    "user_attrs,expected",
    [
        ({"role_ids": [PLATFORM_ADMIN_ROLE_ID]}, True),
        ({"is_platform_admin": True}, False),
    ],
)
def test_historical_migration_seed_uses_role_membership(user_attrs, expected):
    """Legacy user expressions retain their current role-derived meaning."""

    class StubUser:
        def __init__(self, **kw):
            self.user_id = "u1"
            self.email = "x@example.com"
            self.organization_id = None
            self.is_platform_admin = False
            self.role_ids = []
            self.role_names = []
            self.policy_authorities = frozenset()
            for k, v in kw.items():
                setattr(self, k, v)

    policies = TablePolicies.model_validate(_extract_seed_from_migration())
    user = StubUser(**user_attrs)
    for action in ("read", "create", "update", "delete"):
        assert evaluate_action(action, policies, row={}, user=user) is expected, (
            f"action={action!r} user={user_attrs} expected={expected}"
        )


def test_runtime_seed_allows_execution_authority_without_forging_a_role():
    class StubUser:
        user_id = "u1"
        email = "x@example.com"
        organization_id = None
        is_platform_admin = False
        role_ids = []
        role_names = []
        policy_authorities = frozenset({"platform_admin"})

    policies = TablePolicies.model_validate(make_seed_admin_bypass())
    for action in ("read", "create", "update", "delete"):
        assert evaluate_action(action, policies, row={}, user=StubUser())
