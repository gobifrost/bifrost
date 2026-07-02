"""Mechanical enforcement of the Solution runtime-scope pattern.

Three rules (see api/src/repositories/README.md, "How the install id is
DERIVED"):

1. Only core/auth.py parses the raw transport signals: the
   ``X-Bifrost-App`` header and the ``?solution=`` query param must not
   be read anywhere else under api/src.
2. Only services/solution_scope.py and core/auth.py may map
   ``Application.solution_id`` (app -> install) for scope purposes.
3. Nothing re-implements ctx.solution_id parsing: the
   ``UUID(str(ctx.solution_id))`` pattern is owned by
   ``solution_scope.parse_ctx_solution_id``.

These are the load-bearing tripwires that keep install-scope derivation
from drifting back into per-surface vocabularies (the 2026-06/07 class of
"portable refs 404 in deployed apps" bugs). Allow-lists require a
justification comment each; they shrink, never silently grow.
"""
from __future__ import annotations

import re
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[2] / "src"

_RAW_HEADER_RE = re.compile(r"""headers\.get\(\s*['"]X-Bifrost-App['"]""")
_RAW_QUERY_RE = re.compile(r"""query_params\.get\(\s*['"]solution['"]""")
_APP_TO_SOLUTION_RE = re.compile(r"select\(\s*Application\.solution_id\s*\)")
_CTX_PARSE_RE = re.compile(r"UUID\(\s*str\(\s*ctx\.solution_id\s*\)\s*\)")

# THE raw-signal parser: validates UUID shape, refuses inactive installs,
# cross-checks header/param agreement, and sets ctx.solution_id/app_id.
_ALLOWED_FILES_RAW = {
    Path("core/auth.py"),
}

_ALLOWED_FILES_APP_MAP = {
    # Header gate maps app -> solution for the active-install check.
    Path("core/auth.py"),
    # The canonical consumer API (solution_context_id's app fallback,
    # derive_execution_solution_scope's deprecated body-app_id tier).
    Path("services/solution_scope.py"),
}

# parse_ctx_solution_id lives here.
_ALLOWED_FILES_CTX_PARSE = {
    Path("services/solution_scope.py"),
}


def _scan(pattern: re.Pattern, allowed: set[Path]) -> list[str]:
    violations = []
    for py in sorted(API_SRC.rglob("*.py")):
        rel = py.relative_to(API_SRC)
        if rel in allowed:
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if pattern.search(line):
                violations.append(f"{rel}:{i}: {line.strip()}")
    return violations


def test_only_auth_reads_raw_solution_signals():
    v = _scan(_RAW_HEADER_RE, _ALLOWED_FILES_RAW) + _scan(
        _RAW_QUERY_RE, _ALLOWED_FILES_RAW
    )
    assert not v, (
        "Raw ?solution= / X-Bifrost-App parsing outside core/auth.py — "
        "read ctx.solution_id via services/solution_scope.py instead:\n"
        + "\n".join(v)
    )


def test_app_to_solution_mapping_is_canonical():
    v = _scan(_APP_TO_SOLUTION_RE, _ALLOWED_FILES_APP_MAP)
    assert not v, (
        "Application.solution_id scope mapping outside the canonical sites — "
        "use solution_context_id / derive_execution_solution_scope:\n"
        + "\n".join(v)
    )


def test_ctx_solution_id_parse_is_canonical():
    v = _scan(_CTX_PARSE_RE, _ALLOWED_FILES_CTX_PARSE)
    assert not v, (
        "Inline UUID(str(ctx.solution_id)) parse — use "
        "solution_scope.parse_ctx_solution_id:\n" + "\n".join(v)
    )
