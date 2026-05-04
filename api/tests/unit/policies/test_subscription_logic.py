"""Visibility-change four-way fanout decision tests.

Exercises `decide_visibility_change` (the per-message helper) without
standing up a websocket connection. Each case asserts the right action
would be emitted to the subscriber.
"""

from shared.policies.subscription import decide_visibility_change
from src.models.contracts.policies import Expr, TablePolicies
from tests.unit.policies.test_evaluate import FakeUser


def _own_row_policies() -> TablePolicies:
    return TablePolicies.model_validate({
        "policies": [{
            "name": "own_row",
            "actions": ["read"],
            "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
        }]
    })


def test_visibility_stays_in():
    user = FakeUser()
    pol = _own_row_policies()
    row_old = {"id": "r1", "created_by": str(user.user_id), "v": 1}
    row_new = {"id": "r1", "created_by": str(user.user_id), "v": 2}
    decision = decide_visibility_change(row_old, row_new, pol, user)
    assert decision is not None
    assert decision[0] == "update"
    assert decision[1] == row_new


def test_visibility_stays_out():
    user = FakeUser()
    pol = _own_row_policies()
    other = "00000000-0000-0000-0000-000000000999"
    row_old = {"id": "r1", "created_by": other, "v": 1}
    row_new = {"id": "r1", "created_by": other, "v": 2}
    assert decide_visibility_change(row_old, row_new, pol, user) is None


def test_visibility_gain():
    """Row mutates from 'not mine' to 'mine' (e.g. ownership reassign)."""
    user = FakeUser()
    pol = _own_row_policies()
    other = "00000000-0000-0000-0000-000000000999"
    row_old = {"id": "r1", "created_by": other}
    row_new = {"id": "r1", "created_by": str(user.user_id)}
    decision = decide_visibility_change(row_old, row_new, pol, user)
    assert decision is not None
    assert decision[0] == "insert"
    assert decision[1] == row_new


def test_visibility_loss():
    user = FakeUser()
    pol = _own_row_policies()
    other = "00000000-0000-0000-0000-000000000999"
    row_old = {"id": "r1", "created_by": str(user.user_id)}
    row_new = {"id": "r1", "created_by": other}
    decision = decide_visibility_change(row_old, row_new, pol, user)
    assert decision == ("delete", "r1")


def test_user_filter_narrows_visibility():
    """User-passed filter further restricts what the user sees."""
    user = FakeUser()
    pol = _own_row_policies()
    user_filter = Expr.model_validate({"eq": [{"row": "status"}, "open"]})
    row_old = {"id": "r1", "created_by": str(user.user_id), "status": "open"}
    row_new = {"id": "r1", "created_by": str(user.user_id), "status": "done"}
    # Status flipped from open to done → user filter says no longer visible
    decision = decide_visibility_change(row_old, row_new, pol, user, user_filter=user_filter)
    assert decision == ("delete", "r1")
