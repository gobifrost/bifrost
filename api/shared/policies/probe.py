"""High-level policy helpers used by REST handlers and the websocket layer.

These wrap the evaluator and compiler with action-aware logic and provide
the seeded admin-bypass default.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_ as sa_or
from sqlalchemy import true as sa_true
from sqlalchemy.sql import ColumnElement

from shared.policies.compile import compile_to_sql
from shared.policies.evaluate import evaluate
from src.models.contracts.policies import TablePolicies


def evaluate_action(
    action: str,
    policies: TablePolicies,
    row: dict,
    user: Any,
) -> bool:
    """OR across all rules whose `actions` includes `action`. Default deny."""
    for policy in policies.policies:
        if action not in policy.actions:
            continue
        if policy.when is None:
            return True
        if evaluate(policy.when, row=row, user=user):
            return True
    return False


def compile_read_filter(
    policies: TablePolicies,
    user: Any,
) -> ColumnElement | None:
    """Compile the OR of all read-allowing rules into a single WHERE clause.

    Returns None if no policy grants read (the handler must deny).
    """
    fragments: list[ColumnElement] = []
    for policy in policies.policies:
        if "read" not in policy.actions:
            continue
        if policy.when is None:
            fragments.append(sa_true())
            continue
        fragments.append(compile_to_sql(policy.when, user))
    if not fragments:
        return None
    if len(fragments) == 1:
        return fragments[0]
    return sa_or(*fragments)


def is_subscribe_authorized(policies: TablePolicies, user: Any) -> bool:
    """Probe: would ANY read message ever reach this user on this table?

    For row-data-dependent policies, we conservatively allow subscribe and
    let the per-message filter do the actual gating. For user-only policies
    (e.g. is_platform_admin), we resolve at probe time.
    """
    for policy in policies.policies:
        if "read" not in policy.actions:
            continue
        if policy.when is None:
            return True
        if _is_purely_user_dependent(policy.when.root):
            # Resolve immediately — no row context affects the answer
            if evaluate(policy.when, row={}, user=user):
                return True
            continue
        # Row-data-dependent → conservatively allow
        return True
    return False


def _is_purely_user_dependent(node: Any) -> bool:
    """True if the expression references only USER fields and literals."""
    if isinstance(node, (str, int, float, bool)) or node is None:
        return True
    if isinstance(node, list):
        return all(_is_purely_user_dependent(x) for x in node)
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"row"}:
            return False
        if keys == {"user"}:
            return True
        if "call" in keys:
            return all(_is_purely_user_dependent(a) for a in node.get("args", []))
        if len(keys) == 1:
            return _is_purely_user_dependent(node[next(iter(keys))])
    return False


def make_seed_admin_bypass() -> dict:
    """The default policies dict for a freshly-created table.

    Stored verbatim into Table.access at create time. Visible/editable
    in the policy editor; can be removed if an org wants strict audit.
    """
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "description": "Platform admins bypass all checks. Edit or delete to enforce stricter audit.",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            }
        ]
    }
