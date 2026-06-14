"""Static reference scanners for the Solution capture/export dependency walker.

These find the *string* references a workspace file makes to other entities, so
the dependency walker can compute what a capture/export selection pulls in (and,
in reverse, what outside it still points at the selection).

The scans are STATIC and intentionally simple:
- Python module imports reuse the canonical AST scanner in
  ``solution_vendoring`` (workflows -> ``modules/*.py``).
- Entity name/path references (``tables.get("x")``, ``config.get("k")``,
  ``useWorkflow("p::f")``, ``useTable("x")``, ``integrations.get("Name")``) are
  matched as STRING LITERALS via
  regex. Dynamic references built from variables are invisible — which is
  exactly why the capture/export preview is a deselectable human-checked list
  (capture-design §3.3), not an automatic gate.

Matching string literals (not full parses) keeps one scanner working across both
Python and TSX without a TS parser, at the cost of missing computed refs. That
trade-off is the documented design: the human is the authority over the preview.
"""

from __future__ import annotations

import re

from bifrost.solution_vendoring import scan_imported_modules

__all__ = [
    "scan_imported_modules",
    "scan_table_refs",
    "scan_config_refs",
    "scan_workflow_refs",
    "scan_integration_refs",
]

# A quoted string literal, single or double quotes, capturing the inner value.
_STR = r"""['"]([^'"]+)['"]"""

# ``tables.get("name")`` / ``sdk.tables.get("name")`` / ``useTable("name")``.
# The first arg is the table name (``tables.get`` may take a row id as 2nd arg,
# which we ignore). Leading ``sdk.`` is optional.
_TABLE_RE = re.compile(
    rf"""(?:\buseTable\s*\(\s*|\btables\s*\.\s*get\s*\(\s*){_STR}"""
)

# ``config.get("key")`` / ``sdk.config.get("key")``.
_CONFIG_RE = re.compile(rf"""\bconfig\s*\.\s*get\s*\(\s*{_STR}""")

# App workflow hooks (TSX): ``useWorkflow`` / ``useWorkflowQuery`` /
# ``useWorkflowMutation``. The first arg is a workflow IDENTIFIER — either a
# bare name (``'get_clients'``) or a portable ``path::function`` ref — so the
# walker resolves the captured value both ways. ``(?:Query|Mutation)?`` keeps
# the three hook names in one pattern.
_WORKFLOW_RE = re.compile(
    rf"""\buseWorkflow(?:Query|Mutation)?\s*\(\s*{_STR}"""
)


# ``integrations.get("Name")`` / ``sdk.integrations.get("Name")``.
# First arg is the integration NAME (a string literal). Dynamic refs are
# invisible — same documented static-scan tradeoff as configs/tables.
_INTEGRATION_RE = re.compile(
    rf"""\bintegrations\s*\.\s*get\s*\(\s*{_STR}"""
)


def scan_table_refs(source: str) -> set[str]:
    """Return table NAMES referenced by ``source`` (``tables.get``/``useTable``)."""
    return set(_TABLE_RE.findall(source))


def scan_config_refs(source: str) -> set[str]:
    """Return config KEYS referenced by ``source`` (``config.get``)."""
    return set(_CONFIG_RE.findall(source))


def scan_workflow_refs(source: str) -> set[str]:
    """Return workflow identifiers in ``source``.

    Matches ``useWorkflow``/``useWorkflowQuery``/``useWorkflowMutation``; the
    captured value is a bare name OR a ``path::fn`` ref (caller resolves both).
    """
    return set(_WORKFLOW_RE.findall(source))


def scan_integration_refs(source: str) -> set[str]:
    """Return integration NAMES referenced via ``integrations.get(...)``."""
    return set(_INTEGRATION_RE.findall(source))
