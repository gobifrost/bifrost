"""Portable validation for Bifrost managed-file location names.

This module is intentionally free of server imports because Solution manifests
are parsed by the standalone CLI archive as well as by the API server.
"""

from __future__ import annotations

import re


RESERVED_LOCATIONS: frozenset[str] = frozenset({"workspace", "uploads", "temp"})
BLOCKED_LOCATION_NAMES: frozenset[str] = frozenset({"_repo", "_tmp", "_apps"})
FREEFORM_LOCATION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_location_name(location: str) -> None:
    """Validate a reserved location or safe freeform managed-file location."""
    if location in RESERVED_LOCATIONS:
        return
    if location in BLOCKED_LOCATION_NAMES:
        raise ValueError(
            f"Invalid location: '{location}' is a reserved bucket prefix; "
            "use 'workspace', 'temp', or 'uploads' instead."
        )
    if not FREEFORM_LOCATION_NAME_RE.match(location):
        raise ValueError(
            "Invalid location name: "
            f"'{location}' must match {FREEFORM_LOCATION_NAME_RE.pattern}"
        )
