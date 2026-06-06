"""
Shared-dependency vendoring for Export Solution (success-criteria §3.5 / §5,
criterion 5).

A Solution is a self-contained world. When its Python imports modules that live
in the ad-hoc ``_repo/`` shared library (``shared.*`` and friends) rather than
inside the solution, exporting the Solution must VENDOR those modules into the
bundle — copy their source under the solution's own import root — so the export
installs on a *fresh* instance with no ``_repo/`` present and its imports resolve
to the vendored copies (not a silent dependency on the origin's ``_repo/``).

This module provides:
- ``scan_imported_modules``: the static (AST) scan of the full dotted module
  names a source file imports.
- ``vendor_shared_deps``: given the solution's bundle files + a reader over the
  origin ``_repo/``, transitively pull every referenced shared module's source
  into the bundle, keyed by its relative path.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def scan_imported_modules(source: str) -> set[str]:
    """Return the full dotted module names imported by ``source``.

    ``import a.b.c`` -> ``a.b.c``; ``from a.b import c`` -> ``a.b`` (the module
    being imported from). Submodule precision matters for vendoring: a
    ``from shared.calc import X`` must vendor ``shared/calc.py``, not just the
    ``shared`` package root. Relative/unparseable imports contribute nothing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.add(node.module)
                # `from pkg import name` may import a SUBMODULE (pkg/name.py),
                # not just a symbol from pkg/__init__.py — matters for PEP 420
                # namespace packages with no __init__.py. Record pkg.name too so
                # vendoring can resolve the submodule file. ``import *`` (name
                # "*") is not a module and is skipped.
                for alias in node.names:
                    if alias.name and alias.name != "*":
                        mods.add(f"{node.module}.{alias.name}")
    return mods


def _module_candidate_paths(module: str) -> list[str]:
    """Relative repo paths a dotted module name could resolve to."""
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


async def vendor_shared_deps(
    bundle_files: dict[str, str],
    repo_read: Callable[[str], Awaitable[str | None]],
    *,
    solution_local_roots: frozenset[str] = frozenset({"modules", "workflows"}),
) -> dict[str, str]:
    """Vendor referenced ``_repo/`` shared modules into the bundle.

    Args:
        bundle_files: relative path -> source for the solution's own files.
        repo_read: async reader returning ``_repo/<path>`` source, or None if a
            path is absent in the origin repo (stdlib/third-party/typo).
        solution_local_roots: import roots that live inside the solution itself
            (already in the bundle) — never vendored.

    Returns:
        A mapping of newly-vendored ``path -> source`` to merge into the bundle.
        Resolution is transitive: a vendored module's own shared imports are
        vendored too.
    """
    vendored: dict[str, str] = {}
    seen_modules: set[str] = set()

    # Work queue of source blobs whose imports still need scanning. Seed with the
    # solution's own files.
    pending: list[str] = list(bundle_files.values())

    def _already_have(path: str) -> bool:
        return path in bundle_files or path in vendored

    while pending:
        source = pending.pop()
        for module in scan_imported_modules(source):
            if module in seen_modules:
                continue
            seen_modules.add(module)
            root = module.split(".")[0]
            if root in solution_local_roots:
                continue
            # Resolve the module's source from the origin _repo/ at its full
            # dotted path (shared.calc -> shared/calc.py). If absent, it's
            # stdlib/third-party (or a declared dep) — nothing to vendor.
            for cand in _module_candidate_paths(module):
                if _already_have(cand):
                    break
                src = await repo_read(cand)
                if src is not None:
                    vendored[cand] = src
                    pending.append(src)  # transitive scan
                    logger.info("Vendored shared module %s -> %s", module, cand)
                    break

    return vendored
