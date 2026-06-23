"""File-domain policy helpers backed by the shared policy evaluator."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from shared.policies.evaluate import evaluate
from src.models.contracts.policies import FileAction, FilePolicies

FileResolver = Callable[[str], Any]


class _PrefixPolicy(Protocol):
    location: str
    path: str


@dataclass(frozen=True)
class FilePolicyContext:
    """Facts exposed to file policies through the ``{file: ...}`` namespace."""

    location: str
    path: str
    created_by: UUID | str | None = None
    created_at: datetime | str | None = None

    def resolve(self, field: str) -> Any:
        value = getattr(self, field, None)
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value


def make_seed_admin_bypass() -> dict:
    """Default editable bypass policy for new file policy prefixes."""
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "description": (
                    "Platform admins bypass all file checks. Edit or delete "
                    "to enforce stricter audit."
                ),
                "actions": ["read", "write", "delete", "list"],
                "when": {"user": "is_platform_admin"},
            }
        ]
    }


def select_longest_prefix(
    candidates: Iterable[_PrefixPolicy],
    location: str,
    path: str,
) -> _PrefixPolicy | None:
    """Return the most specific policy prefix for ``location/path``.

    Prefix matching is path-segment aware: ``reports`` matches
    ``reports/q1.pdf`` but not the sibling ``reports2/q1.pdf``.
    """
    best: _PrefixPolicy | None = None
    best_len = -1
    for candidate in candidates:
        if candidate.location != location:
            continue
        if not _path_matches(candidate.path, path):
            continue
        normalized_len = len(_normalize_path(candidate.path))
        if normalized_len > best_len:
            best = candidate
            best_len = normalized_len
    return best


def evaluate_file_action(
    action: FileAction,
    policies: FilePolicies,
    file: FilePolicyContext,
    user: Any,
) -> bool:
    """OR across file policy rules for one action. Default deny."""
    for policy in policies.policies:
        if action not in policy.actions:
            continue
        if policy.when is None:
            return True
        if evaluate(
            policy.when,  # type: ignore[arg-type]
            row={},
            user=user,
            resolvers={"file": file.resolve},
        ):
            return True
    return False


def _path_matches(prefix: str, path: str) -> bool:
    normalized_prefix = _normalize_path(prefix)
    normalized_path = _normalize_path(path)
    if normalized_prefix == "":
        return True
    return (
        normalized_path == normalized_prefix
        or normalized_path.startswith(f"{normalized_prefix}/")
    )


def _normalize_path(path: str) -> str:
    return path.strip("/")
