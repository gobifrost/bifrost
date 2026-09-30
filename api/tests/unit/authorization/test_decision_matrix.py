"""Persona x operation matrix: the R2c evaluator against today's behaviour.

For every access-list entry, every persona and every target, the evaluator
must decide what today's code decides (``legacy_oracle``), except:

- the listed difference: a provider-org member on an entry that records an
  ``intended_change`` (the provider-org non-admin path goes away at R3), and
- ``PENDING_DECISION``: cells whose difference is a who-can-do-what question
  awaiting a decision, listed explicitly so the rest of the matrix stays
  strict.
"""

from __future__ import annotations

from collections import defaultdict

from src.models.contracts.access_list import AccessEntry
from src.services.access_list import ACCESS_LIST, effective_entries
from src.services.authorization.evaluator import GLOBAL, HOME, Target, cross_org, decide
from tests.unit.authorization.legacy_oracle import (
    OTHER_CUSTOMER_ORG_ID,
    PERSONAS,
    Persona,
    legacy_decide,
)

TARGETS: tuple[tuple[str, Target, bool], ...] = (
    ("home", HOME, False),
    ("cross_org", cross_org(OTHER_CUSTOMER_ORG_ID), True),
    ("cross_global", GLOBAL, True),
)

_DECIDERS = dict(zip((e.key for e in ACCESS_LIST), effective_entries(ACCESS_LIST)))

_NON_ADMINS = frozenset({"provider_member", "regular", "external"})

# Cells whose difference is a who-can-do-what decision at R3: entry key ->
# personas excused (at every target). Each entry shares a read permission with
# operations every signed-in user can reach, so the User role holds it.
PENDING_DECISION: dict[tuple[str, str] | str, frozenset[str]] = {
    ("GET", "/api/admin/required-instructions/organizations/{organization_id}"): _NON_ADMINS,
    ("GET", "/api/admin/roi/settings"): _NON_ADMINS,
    ("GET", "/api/agent-runs/backfill-eligible"): _NON_ADMINS,
    ("GET", "/api/agent-runs/backfill-jobs"): _NON_ADMINS,
    ("GET", "/api/agent-runs/backfill-jobs/{job_id}"): _NON_ADMINS,
    ("GET", "/api/applications/{app_id}/embed-secrets"): _NON_ADMINS,
    ("GET", "/api/applications/{app_id}/source"): _NON_ADMINS,
    ("GET", "/api/decorator-properties"): _NON_ADMINS,
    ("GET", "/api/forms/{form_id}/embed-secrets"): _NON_ADMINS,
    ("GET", "/api/forms/{form_id}/publication"): _NON_ADMINS,
    ("GET", "/api/forms/{form_id}/publication-review"): _NON_ADMINS,
    ("GET", "/api/mcp/config"): _NON_ADMINS,
    ("GET", "/api/reports/roi/by-organization"): _NON_ADMINS,
    ("GET", "/api/reports/roi/by-workflow"): _NON_ADMINS,
    ("GET", "/api/reports/roi/summary"): _NON_ADMINS,
    ("GET", "/api/reports/roi/trends"): _NON_ADMINS,
    ("GET", "/api/settings/oauth"): _NON_ADMINS,
    ("GET", "/api/settings/oauth/{provider}"): _NON_ADMINS,
    ("GET", "/api/workflow-keys"): _NON_ADMINS,
}


def _label(entry: AccessEntry) -> str:
    return entry.mcp_tool or f"{entry.method} {entry.path}"


def _cells():
    for entry in ACCESS_LIST:
        decider = _DECIDERS[entry.key]
        for persona in PERSONAS:
            for target_name, target, cross in TARGETS:
                legacy = legacy_decide(persona, decider, cross)
                decision = decide(persona.ctx, decider, target)
                yield entry, decider, persona, target_name, legacy, decision


def _is_pending(entry: AccessEntry, persona: Persona) -> bool:
    return persona.name in PENDING_DECISION.get(entry.key, frozenset())


def _is_listed_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return persona.name == "provider_member" and decider.intended_change is not None and legacy and not allowed


def _row(entry: AccessEntry, persona: Persona, target_name: str, legacy: bool, decision) -> str:
    return f"{persona.name} / {_label(entry)} / {target_name} / legacy={legacy} / new={decision.allowed} / {decision.rule}"


def test_evaluator_matches_legacy_except_listed_differences() -> None:
    mismatches = []
    for entry, decider, persona, target_name, legacy, decision in _cells():
        if legacy == decision.allowed:
            continue
        if _is_listed_difference(decider, persona, legacy, decision.allowed):
            continue
        if _is_pending(entry, persona):
            continue
        mismatches.append(_row(entry, persona, target_name, legacy, decision))
    assert not mismatches, f"{len(mismatches)} unexpected differences:\n" + "\n".join(mismatches)


def test_every_intended_change_marker_produces_a_difference() -> None:
    differing: dict = defaultdict(bool)
    for entry, decider, persona, _, legacy, decision in _cells():
        if _is_listed_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key] = True
    stale = [_label(e) for e in ACCESS_LIST if _DECIDERS[e.key].intended_change and not differing[e.key]]
    assert not stale, f"intended_change set but no difference produced: {stale}"


def test_pending_decision_entries_still_differ() -> None:
    differing: dict = defaultdict(set)
    for entry, decider, persona, _, legacy, decision in _cells():
        if legacy != decision.allowed and not _is_listed_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key].add(persona.name)
    stale = [
        (key, sorted(personas - differing[key]))
        for key, personas in PENDING_DECISION.items()
        if personas - differing[key]
    ]
    assert not stale, f"pending-decision personas that no longer differ: {stale}"
