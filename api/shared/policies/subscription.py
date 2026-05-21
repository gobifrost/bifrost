"""Per-message visibility decision for subscriptions.

Domain-agnostic. The four-way visibility transition table
(old_visible × new_visible → emitted action) is the same for any
domain that ships row-like change events.
"""
from __future__ import annotations

from typing import Any, Literal

from shared.policies.ast import Expr, PolicyDocument
from shared.policies.evaluate import evaluate
from shared.policies.probe import evaluate_action
from shared.policies.resolver import Resolver

Action = Literal["insert", "update", "delete"]


def is_row_visible(
    ctx: dict | None,
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
    user_filter: Expr | None = None,
) -> bool:
    """True iff ctx is readable AND passes the user-supplied filter."""
    if ctx is None:
        return False
    if not evaluate_action("read", policies, ctx, user, resolver):
        return False
    if user_filter is not None and not evaluate(user_filter, ctx=ctx, user=user, resolver=resolver):
        return False
    return True


def decide_visibility_change(
    old_ctx: dict | None,
    new_ctx: dict | None,
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
    user_filter: Expr | None = None,
) -> tuple[Action, dict | str | None] | None:
    """Compute the four-way fanout decision."""
    old_visible = is_row_visible(old_ctx, policies, user, resolver, user_filter)
    new_visible = is_row_visible(new_ctx, policies, user, resolver, user_filter)

    if not old_visible and not new_visible:
        return None
    if not old_visible and new_visible:
        return ("insert", new_ctx)
    if old_visible and not new_visible:
        return ("delete", (old_ctx or {}).get("id"))
    return ("update", new_ctx)
