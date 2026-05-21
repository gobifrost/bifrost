"""Action-level policy helpers. Domain-agnostic.

`evaluate_action` and `is_subscribe_authorized` walk the rules in a
PolicyDocument and dispatch to `evaluate` (which itself dispatches to a
Resolver). Domain-specific helpers (e.g. `compile_read_filter`,
`make_seed_admin_bypass`) live in the domain module.
"""
from __future__ import annotations

from typing import Any

from shared.policies.ast import PolicyDocument
from shared.policies.evaluate import evaluate
from shared.policies.resolver import Resolver


def evaluate_action(
    action: str,
    policies: PolicyDocument,
    ctx: Any,
    user: Any,
    resolver: Resolver,
) -> bool:
    """OR across all rules whose `actions` includes `action`. Default deny."""
    for policy in policies.policies:
        if action not in policy.actions:
            continue
        if policy.when is None:
            return True
        if evaluate(policy.when, ctx=ctx, user=user, resolver=resolver):
            return True
    return False


def is_subscribe_authorized(
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
) -> bool:
    """Probe: would ANY read message ever reach this user?"""
    for policy in policies.policies:
        if "read" not in policy.actions:
            continue
        if policy.when is None:
            return True
        if _is_purely_user_dependent(policy.when.root, resolver):
            if evaluate(policy.when, ctx={}, user=user, resolver=resolver):
                return True
            continue
        return True
    return False


def _is_purely_user_dependent(node: Any, resolver: Resolver) -> bool:
    """True if the expression references only USER fields and literals."""
    if isinstance(node, (str, int, float, bool)) or node is None:
        return True
    if isinstance(node, list):
        return all(_is_purely_user_dependent(x, resolver) for x in node)
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {resolver.namespace}:
            return False
        if keys == {"user"}:
            return True
        if "call" in keys:
            return all(_is_purely_user_dependent(a, resolver) for a in node.get("args", []))
        if len(keys) == 1:
            return _is_purely_user_dependent(node[next(iter(keys))], resolver)
    return False
