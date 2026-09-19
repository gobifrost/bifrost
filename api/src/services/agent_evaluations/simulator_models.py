"""Versioned synthetic fixture models for the stateful tool simulator.

A fixture owns coherent mutable entity collections plus deterministic
ID/time sources. Baseline and candidate runs each start from an
independent deep copy of the exact same frozen fixture, so repeated runs
are byte-stable except for explicitly ignored fields.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from typing import Any

FIXTURE_VERSION = 1

SECRET_KEY_HINTS = ("secret", "token", "password", "api_key", "apikey", "credential")
TOKEN_COUNTER_KEYS = frozenset({
    "tokens", "input_tokens", "output_tokens", "total_tokens", "max_tokens",
    "default_max_tokens", "cache_read_tokens", "cache_write_tokens", "max_token_budget",
})


def canonical_hash(value: Any) -> str:
    """Stable SHA-256 over a JSON-canonical encoding (sorted keys)."""
    import json

    canonical = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def redact_value(value: Any) -> Any:
    """Replace secret-bearing values with ``[REDACTED]`` (key-name based)."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(hint in key.lower() for hint in SECRET_KEY_HINTS)
                and not (
                    key in TOKEN_COUNTER_KEYS
                    and (item is None or isinstance(item, (int, float)))
                )
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def validate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on malformed fixtures at case save time."""
    if not isinstance(fixture, dict):
        raise FixtureError("Fixture must be an object.")
    version = fixture.get("version", FIXTURE_VERSION)
    if version != FIXTURE_VERSION:
        raise FixtureError(
            f"Unsupported fixture version {version!r}; simulator supports "
            f"{FIXTURE_VERSION}."
        )
    if "seed_time" in fixture:
        try:
            seed_time = datetime.fromisoformat(fixture["seed_time"])
        except (TypeError, ValueError) as exc:
            raise FixtureError("Fixture seed_time must be an ISO timestamp.") from exc
        if seed_time.tzinfo is None:
            raise FixtureError("Fixture seed_time must include a timezone.")
    ticks = fixture.get("clock_ticks", 0)
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
        raise FixtureError("Fixture clock_ticks must be a nonnegative integer.")
    entities = fixture.get("entities", {})
    if not isinstance(entities, dict):
        raise FixtureError("Fixture 'entities' must be an object of collections.")
    for collection, rows in entities.items():
        if not isinstance(rows, dict):
            raise FixtureError(
                f"Fixture collection {collection!r} must map IDs to objects."
            )
    allowed = fixture.get("allowed_tools", [])
    if not isinstance(allowed, list) or not all(isinstance(t, str) for t in allowed):
        raise FixtureError("Fixture 'allowed_tools' must be a list of tool names.")
    rules = fixture.get("rules", [])
    if not isinstance(rules, list):
        raise FixtureError("Fixture 'rules' must be a list.")
    for rule in rules:
        if not isinstance(rule, dict) or "tool" not in rule:
            raise FixtureError("Every fixture rule must be an object with a 'tool'.")
        unknown = set(rule) - {"tool", "match_args", "return", "mutate"}
        if unknown:
            raise FixtureError(f"Unsupported fixture rule fields: {', '.join(sorted(unknown))}.")
        if not isinstance(rule["tool"], str) or not rule["tool"].strip():
            raise FixtureError("Fixture rule tool must be a nonempty name.")
        if not isinstance(rule.get("match_args", {}), dict):
            raise FixtureError("Fixture rule match_args must be an object.")
        if not isinstance(rule.get("mutate", []), list):
            raise FixtureError("Fixture rule mutate must be a list.")
    return fixture


class FixtureError(Exception):
    """A fixture is malformed and cannot seed a simulation."""


def fresh_state(fixture: dict[str, Any]) -> dict[str, Any]:
    """Independent deep copy of the frozen fixture for one run side."""
    return copy.deepcopy(fixture)
