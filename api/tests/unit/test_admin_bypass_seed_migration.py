"""Pin the seed JSON used by both the create handler and the backfill migration.

`make_seed_admin_bypass()` is the runtime helper for new tables; the 2026-05-04
backfill migration hard-codes the same shape into a SQL UPDATE. The two paths
must produce identical TablePolicies. This test fails loudly if either side
drifts.
"""
import json
from pathlib import Path

import pytest

from shared.policies.probe import make_seed_admin_bypass
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


def test_migration_seed_matches_runtime_helper():
    migration_seed = _extract_seed_from_migration()
    runtime_seed = make_seed_admin_bypass()
    assert migration_seed == runtime_seed, (
        "20260504_backfill_table_access drifted from make_seed_admin_bypass(). "
        "Update one or the other so the migration backfills the same shape "
        "the create handler writes for new tables."
    )


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
        ({"is_platform_admin": True}, True),
        ({"is_platform_admin": False}, False),
    ],
)
def test_migration_seed_admin_only_evaluation(user_attrs, expected):
    """Behaviorally: the seed grants every action to admins, denies all others."""
    from shared.policies.probe import evaluate_action

    class StubUser:
        def __init__(self, **kw):
            self.user_id = "u1"
            self.email = "x@example.com"
            self.organization_id = None
            self.is_platform_admin = False
            self.role_ids = []
            self.role_names = []
            for k, v in kw.items():
                setattr(self, k, v)

    policies = TablePolicies.model_validate(_extract_seed_from_migration())
    user = StubUser(**user_attrs)
    for action in ("read", "create", "update", "delete"):
        assert evaluate_action(action, policies, row={}, user=user) is expected, (
            f"action={action!r} user={user_attrs} expected={expected}"
        )
