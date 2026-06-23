"""Seed policy for a freshly-created file share/prefix.

Mirrors `shared.policies.probe.make_seed_admin_bypass` for Tables, but uses
the file action vocabulary (read/write/delete/list). Stored verbatim into the
new FilePolicy at create time so a platform admin is allowed by a VISIBLE,
revocable rule — there is no hardcoded bypass in the evaluator.
"""

from __future__ import annotations


def make_seed_admin_bypass_file() -> dict:
    """New file prefixes reference the built-in admin_bypass (file domain)."""
    return {"policies": [{"$ref": "admin_bypass"}]}
