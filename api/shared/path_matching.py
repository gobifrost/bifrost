"""Path-containment helpers shared by policy selection and evaluation."""

from __future__ import annotations


def normalize_path(path: str) -> str:
    """Remove surrounding separators without changing path segments."""
    return path.strip("/")


def path_matches_prefix(prefix: str, path: str) -> bool:
    """Return whether ``path`` equals or descends from ``prefix``.

    An empty prefix intentionally matches every path because file-policy
    selection uses it for a location's root policy. Claim-backed authorization
    must use :func:`path_within_any`, which rejects empty grant prefixes.
    """
    normalized_prefix = normalize_path(prefix)
    normalized_path = normalize_path(path)
    if normalized_prefix == "":
        return True
    return normalized_path == normalized_prefix or normalized_path.startswith(
        f"{normalized_prefix}/"
    )


def path_within_any(path: object, prefixes: object) -> bool:
    """Return whether a string path is within any non-empty string prefix.

    Invalid values fail closed. Ancestor lookup makes matching scale with path
    depth after one linear normalization of the user's claim values.
    """
    if not isinstance(path, str) or not isinstance(prefixes, list):
        return False

    allowed = {
        normalized
        for prefix in prefixes
        if isinstance(prefix, str) and (normalized := normalize_path(prefix))
    }
    if not allowed:
        return False

    candidate = normalize_path(path)
    while True:
        if candidate in allowed:
            return True
        if "/" not in candidate:
            return False
        candidate = candidate.rsplit("/", 1)[0]
