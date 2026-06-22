"""Seed policy for a freshly-created file share/prefix.

Mirrors `shared.policies.probe.make_seed_admin_bypass` for Tables, but uses
the file action vocabulary (read/write/delete/list). Stored verbatim into the
new FilePolicy at create time so a platform admin is allowed by a VISIBLE,
revocable rule — there is no hardcoded bypass in the evaluator.
"""

from __future__ import annotations


def make_seed_admin_bypass_file() -> dict:
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "description": (
                    "Platform admins bypass all checks. "
                    "Edit or delete to enforce stricter access."
                ),
                "actions": ["read", "write", "delete", "list"],
                "when": {"user": "is_platform_admin"},
            }
        ]
    }
