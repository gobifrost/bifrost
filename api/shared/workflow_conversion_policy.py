"""Conservative prospective evaluation for resolved table and file policies."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from shared.policies.evaluate import evaluate
from src.core.principal import UserPrincipal
from src.models.contracts.policies import (
    Action,
    Expr,
    FileAction,
    FileExpr,
    FilePolicies,
    FilePolicyRule,
    Policy,
    PolicyRuleRef,
    TablePolicies,
)


_DEPTH_LIMIT = 64
_ReasonKind = Literal["row", "claim", "user", "file", "unresolved_rule", "depth"]
_ResultKind = Literal["allowed", "denied", "conditional"]


@dataclass(frozen=True)
class ProspectivePolicyReason:
    """A specific fact absent from a prospective policy analysis."""

    path: str
    kind: _ReasonKind


@dataclass(frozen=True)
class ProspectivePolicyResult:
    """Whether a policy can be decided without resource-time facts."""

    kind: _ResultKind
    reasons: tuple[ProspectivePolicyReason, ...] = ()


_ALLOWED = ProspectivePolicyResult("allowed")
_DENIED = ProspectivePolicyResult("denied")


def analyze_table_policies(
    policies: TablePolicies,
    action: Action,
    principal: UserPrincipal,
    *,
    known_user_fields: Set[str] = frozenset(),
    known_claims: Mapping[str, list[object]] | None = None,
) -> ProspectivePolicyResult:
    """Prospectively evaluate resolved table policies for one action.

    Row references remain conditional because a conversion has no document.
    Only explicitly supplied user fields and claims may participate in a
    definitive result.
    """
    return _analyze_policies(
        policies.policies,
        action,
        principal,
        known_user_fields=known_user_fields,
        known_claims=known_claims or {},
        known_file_fields=None,
    )


def analyze_file_policies(
    policies: FilePolicies,
    action: FileAction,
    principal: UserPrincipal,
    *,
    known_user_fields: Set[str] = frozenset(),
    known_claims: Mapping[str, list[object]] | None = None,
    known_file_fields: Mapping[str, object] | None = None,
) -> ProspectivePolicyResult:
    """Prospectively evaluate resolved file policies for one action.

    ``known_file_fields`` is intentionally limited by its caller to bounded
    selectors such as ``path`` and ``location``. Missing metadata, including
    ``created_by``, remains conditional.
    """
    return _analyze_policies(
        policies.policies,
        action,
        principal,
        known_user_fields=known_user_fields,
        known_claims=known_claims or {},
        known_file_fields=known_file_fields or {},
    )


def _analyze_policies(
    policies: list[Policy | FilePolicyRule | PolicyRuleRef],
    action: str,
    principal: UserPrincipal,
    *,
    known_user_fields: Set[str],
    known_claims: Mapping[str, list[object]],
    known_file_fields: Mapping[str, object] | None,
) -> ProspectivePolicyResult:
    synthetic = replace(principal, policy_authorities=frozenset())
    # The canonical evaluator reads claims from the principal. This copy is
    # local to the pure analysis and exposes only explicitly supplied claims.
    synthetic.claims = dict(known_claims)  # type: ignore[attr-defined]

    conditional: list[ProspectivePolicyReason] = []
    for index, policy in enumerate(policies):
        policy_path = f"policies[{index}]"
        if isinstance(policy, PolicyRuleRef):
            conditional.append(ProspectivePolicyReason(policy_path, "unresolved_rule"))
            continue
        if action not in policy.actions:
            continue
        if policy.when is None:
            return _ALLOWED
        outcome = _analyze_expr(
            policy.when.root,
            expr=policy.when,
            principal=synthetic,
            known_user_fields=known_user_fields,
            known_claims=known_claims,
            known_file_fields=known_file_fields,
            path=f"{policy_path}.when",
        )
        if outcome.kind == "allowed":
            return _ALLOWED
        if outcome.kind == "conditional":
            conditional.extend(outcome.reasons)

    if conditional:
        return ProspectivePolicyResult("conditional", _dedupe_reasons(conditional))
    return _DENIED


def _analyze_expr(
    node: Any,
    *,
    expr: Expr | FileExpr,
    principal: UserPrincipal,
    known_user_fields: Set[str],
    known_claims: Mapping[str, list[object]],
    known_file_fields: Mapping[str, object] | None,
    path: str,
    depth: int = 0,
) -> ProspectivePolicyResult:
    reasons = _unknown_reasons(
        node,
        known_user_fields=known_user_fields,
        known_claims=known_claims,
        known_file_fields=known_file_fields,
        path=path,
        depth=depth,
    )
    if not reasons:
        return _evaluate_known(node, expr, principal, known_file_fields)

    if depth >= _DEPTH_LIMIT:
        return ProspectivePolicyResult("conditional", _dedupe_reasons(reasons))
    if isinstance(node, dict) and set(node) == {"and"}:
        branches = [
            _analyze_expr(
                item,
                expr=expr,
                principal=principal,
                known_user_fields=known_user_fields,
                known_claims=known_claims,
                known_file_fields=known_file_fields,
                path=f"{path}.and[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(node["and"])
        ]
        if any(branch.kind == "denied" for branch in branches):
            return _DENIED
        if all(branch.kind == "allowed" for branch in branches):
            return _ALLOWED
        return _conditional_from(branches)
    if isinstance(node, dict) and set(node) == {"or"}:
        branches = [
            _analyze_expr(
                item,
                expr=expr,
                principal=principal,
                known_user_fields=known_user_fields,
                known_claims=known_claims,
                known_file_fields=known_file_fields,
                path=f"{path}.or[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(node["or"])
        ]
        if any(branch.kind == "allowed" for branch in branches):
            return _ALLOWED
        if all(branch.kind == "denied" for branch in branches):
            return _DENIED
        return _conditional_from(branches)
    if isinstance(node, dict) and set(node) == {"not"}:
        branch = _analyze_expr(
            node["not"],
            expr=expr,
            principal=principal,
            known_user_fields=known_user_fields,
            known_claims=known_claims,
            known_file_fields=known_file_fields,
            path=f"{path}.not",
            depth=depth + 1,
        )
        if branch.kind == "conditional":
            return branch
        return _DENIED if branch.kind == "allowed" else _ALLOWED
    return ProspectivePolicyResult("conditional", _dedupe_reasons(reasons))


def _evaluate_known(
    node: Any,
    expr: Expr | FileExpr,
    principal: UserPrincipal,
    known_file_fields: Mapping[str, object] | None,
) -> ProspectivePolicyResult:
    resolvers = None
    if known_file_fields is not None:
        resolvers = {"file": known_file_fields.__getitem__}
    # ``evaluate`` is the canonical implementation of comparisons, calls, and
    # null semantics. The source expression type is only a validated root holder.
    known_expr = cast(Expr, type(expr).model_construct(root=node))
    return _ALLOWED if evaluate(known_expr, row={}, user=principal, resolvers=resolvers) else _DENIED


def _unknown_reasons(
    node: Any,
    *,
    known_user_fields: Set[str],
    known_claims: Mapping[str, list[object]],
    known_file_fields: Mapping[str, object] | None,
    path: str,
    depth: int,
) -> tuple[ProspectivePolicyReason, ...]:
    if depth >= _DEPTH_LIMIT:
        return (ProspectivePolicyReason(path, "depth"),)
    if isinstance(node, (str, int, float, bool)) or node is None:
        return ()
    if isinstance(node, list):
        return _dedupe_reasons([
            reason
            for index, item in enumerate(node)
            for reason in _unknown_reasons(
                item,
                known_user_fields=known_user_fields,
                known_claims=known_claims,
                known_file_fields=known_file_fields,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
        ])
    if not isinstance(node, dict):
        return (ProspectivePolicyReason(path, "depth"),)

    keys = set(node)
    if keys == {"row"}:
        return (ProspectivePolicyReason(path, "row"),)
    if keys == {"user"}:
        if node["user"] in known_user_fields:
            return ()
        return (ProspectivePolicyReason(path, "user"),)
    if keys == {"authority"}:
        if "is_platform_admin" in known_user_fields:
            return ()
        return (ProspectivePolicyReason(path, "user"),)
    if keys == {"claims"}:
        if node["claims"] in known_claims:
            return ()
        return (ProspectivePolicyReason(path, "claim"),)
    if keys == {"file"}:
        if known_file_fields is not None and node["file"] in known_file_fields:
            return ()
        return (ProspectivePolicyReason(path, "file"),)
    if "call" in keys:
        reasons: list[ProspectivePolicyReason] = []
        if node["call"] == "has_role":
            if not {"role_ids", "role_names"} <= set(known_user_fields):
                reasons.append(ProspectivePolicyReason(path, "user"))
        for index, argument in enumerate(node.get("args", [])):
            reasons.extend(_unknown_reasons(
                argument,
                known_user_fields=known_user_fields,
                known_claims=known_claims,
                known_file_fields=known_file_fields,
                path=f"{path}.args[{index}]",
                depth=depth + 1,
            ))
        return _dedupe_reasons(reasons)
    return _unknown_reasons(
        next(iter(node.values())),
        known_user_fields=known_user_fields,
        known_claims=known_claims,
        known_file_fields=known_file_fields,
        path=f"{path}.{next(iter(node))}",
        depth=depth + 1,
    )


def _conditional_from(branches: list[ProspectivePolicyResult]) -> ProspectivePolicyResult:
    return ProspectivePolicyResult(
        "conditional",
        _dedupe_reasons([
            reason
            for branch in branches
            if branch.kind == "conditional"
            for reason in branch.reasons
        ]),
    )


def _dedupe_reasons(
    reasons: list[ProspectivePolicyReason] | tuple[ProspectivePolicyReason, ...],
) -> tuple[ProspectivePolicyReason, ...]:
    return tuple(dict.fromkeys(reasons))
