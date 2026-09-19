"""Versioned synthetic fixture models for the stateful tool simulator.

A fixture owns coherent mutable entity collections plus deterministic
ID/time sources. Baseline and candidate runs each start from an
independent deep copy of the exact same frozen fixture, so repeated runs
are byte-stable except for explicitly ignored fields.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

FIXTURE_VERSION = 1

SECRET_KEY_HINTS = ("secret", "token", "password", "api_key", "apikey", "credential")


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
    return fixture


class FixtureError(Exception):
    """A fixture is malformed and cannot seed a simulation."""


def fresh_state(fixture: dict[str, Any]) -> dict[str, Any]:
    """Independent deep copy of the frozen fixture for one run side."""
    return copy.deepcopy(fixture)
