"""Canonical gitignore-style skip patterns for workspace file collection.

Standalone (no ``src.*`` imports) so both the client-side CLI
(``bifrost.cli`` push/pull/sync/watch) and server-side code (Solution
``capture``) share ONE source of truth for "files that must never be
collected/serialized" — build output, caches, editor turds, and most
importantly secret files (``.env``).

Patterns are ``gitwildmatch`` (the syntax ``pathspec`` parses), so callers
build a matcher with ``pathspec.PathSpec.from_lines("gitwildmatch", lines)``.
"""

from __future__ import annotations

#: Applied even without a .gitignore file. ``.bifrost/`` is import/export-only
#: and is never part of push/pull/sync/watch. ``.env``/``.env.*`` hold secrets
#: and must never leave the workspace via sync OR Solution capture/export.
DEFAULT_IGNORE_PATTERNS: list[str] = [
    ".git/",
    # Agent state can contain full conversation transcripts, tool results,
    # credentials, and caches. Keep explicit entries here as defense in depth
    # for collectors that use this canonical list without the CLI's blanket
    # hidden-path rule (for example Solution capture).
    ".claude/",
    ".codex/",
    "__pycache__/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".pyright/",
    "node_modules/",
    ".bifrost/",
    ".venv/",
    "venv/",
    ".tox/",
    "build/",
    "dist/",
    "coverage/",
    ".coverage",
    ".DS_Store",
    "*.pyc",
    # Secret material — never collect into a sync push or a captured/exported
    # Solution bundle. Covers `.env`, `.env.local`, `.env.production`, etc.
    ".env",
    ".env.*",
    # Editor atomic-write turds (e.g. foo.tsx.tmp.12345.1776000000000).
    # Without this, watchdog sees these files and pushes them to S3; the
    # editor then renames them to the real file and watchdog emits a 'moved'
    # event that deliberately does NOT delete the source. Result: every save
    # leaves a turd in S3 forever.
    "*.tmp.*",
    "*.swp",
    "*.swo",
    "*~",
    ".#*",
]
