"""Per-message visibility decision for table subscriptions.

The websocket layer receives `document_change` events with both the pre-mutation
row (`old_row`) and the post-mutation row (`new_row`). For each subscriber, the
visibility of these two rows under the table's policies (and any user-supplied
filter) determines which event — if any — to deliver:

    old_visible | new_visible | emitted action
    ----------- | ----------- | --------------
    False       | False       | (nothing — row stays out of view)
    False       | True        | "insert" (row becomes visible)
    True        | False       | "delete" (row leaves visibility)
    True        | True        | "update" (row stays in view, content changed)

This module centralizes that logic so it can be unit-tested without standing
up a websocket connection. The websocket router calls `decide_visibility_change`
once per message per subscriber.
"""

from __future__ import annotations

from typing import Any, Literal

from shared.policies.evaluate import evaluate
from shared.policies.probe import evaluate_action
from src.models.contracts.policies import Expr, TablePolicies

Action = Literal["insert", "update", "delete"]


def is_row_visible(
    row: dict | None,
    policies: TablePolicies,
    user: Any,
    user_filter: Expr | None = None,
) -> bool:
    """True iff the row is readable AND passes the user-supplied filter.

    A None row is never visible (used for inserts' old side and deletes' new side).
    """
    if row is None:
        return False
    if not evaluate_action("read", policies, row, user):
        return False
    if user_filter is not None and not evaluate(user_filter, row=row, user=user):
        return False
    return True


def decide_visibility_change(
    old_row: dict | None,
    new_row: dict | None,
    policies: TablePolicies,
    user: Any,
    user_filter: Expr | None = None,
) -> tuple[Action, dict | str | None] | None:
    """Compute the four-way fanout decision.

    Returns:
        None if the row is not (and was not) visible to this user.
        ("insert", new_row) if visibility was gained.
        ("update", new_row) if visibility persisted.
        ("delete", old_row_id) if visibility was lost.
    """
    old_visible = is_row_visible(old_row, policies, user, user_filter)
    new_visible = is_row_visible(new_row, policies, user, user_filter)

    if not old_visible and not new_visible:
        return None
    if not old_visible and new_visible:
        return ("insert", new_row)
    if old_visible and not new_visible:
        return ("delete", (old_row or {}).get("id"))
    return ("update", new_row)
