"""Persona x operation matrix: the R2c evaluator against today's behaviour.

For every access-list entry, every persona and every target, the evaluator
must decide what today's code decides (``legacy_oracle``), except the two
listed differences:

- a provider-org member on an entry that records an ``intended_change`` (the
  provider-org non-admin path goes away at R3), and
- a Platform Admin on an entry gated by a permission the wildcard does not
  satisfy (``WILDCARD_EXCLUDED_PERMISSIONS``: secret decryption must be
  assigned explicitly, never implied by the admin base role).
"""

from __future__ import annotations

from collections import defaultdict

from src.models.contracts.access_list import AccessEntry
from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS
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


def _is_provider_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return persona.name == "provider_member" and decider.intended_change is not None and legacy and not allowed


def _is_wildcard_exclusion_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return (
        persona.name == "platform_admin"
        and decider.permission in WILDCARD_EXCLUDED_PERMISSIONS
        and legacy
        and not allowed
    )


def _is_listed_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return _is_provider_difference(decider, persona, legacy, allowed) or _is_wildcard_exclusion_difference(
        decider, persona, legacy, allowed
    )


def _row(entry: AccessEntry, persona: Persona, target_name: str, legacy: bool, decision) -> str:
    return f"{persona.name} / {_label(entry)} / {target_name} / legacy={legacy} / new={decision.allowed} / {decision.rule}"


def test_evaluator_matches_legacy_except_listed_differences() -> None:
    mismatches = []
    for entry, decider, persona, target_name, legacy, decision in _cells():
        if legacy == decision.allowed:
            continue
        if _is_listed_difference(decider, persona, legacy, decision.allowed):
            continue
        mismatches.append(_row(entry, persona, target_name, legacy, decision))
    assert not mismatches, f"{len(mismatches)} unexpected differences:\n" + "\n".join(mismatches)


def test_every_intended_change_marker_produces_a_difference() -> None:
    differing: dict = defaultdict(bool)
    for entry, decider, persona, _, legacy, decision in _cells():
        if _is_provider_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key] = True
    stale = [_label(e) for e in ACCESS_LIST if _DECIDERS[e.key].intended_change and not differing[e.key]]
    assert not stale, f"intended_change set but no difference produced: {stale}"


def test_every_wildcard_excluded_entry_produces_a_difference() -> None:
    differing: dict = defaultdict(bool)
    for entry, decider, persona, _, legacy, decision in _cells():
        if _is_wildcard_exclusion_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key] = True
    excluded = [e for e in ACCESS_LIST if _DECIDERS[e.key].permission in WILDCARD_EXCLUDED_PERMISSIONS]
    assert excluded, "expected at least one entry gated by a wildcard-excluded permission"
    stale = [_label(e) for e in excluded if not differing[e.key]]
    assert not stale, f"wildcard-excluded permission but no difference produced: {stale}"
