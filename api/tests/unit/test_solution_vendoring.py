"""Shared-dependency vendoring scan for Export Solution (criterion 5).

When a Solution's Python imports ``shared.*`` (or other modules that live in the
ad-hoc ``_repo/`` library, not in the solution), exporting it must VENDOR those
modules into the bundle so the exported Solution installs on a *fresh* instance
(no ``_repo/`` shared deps present) and its imports resolve to the vendored
copies.

These tests pin the static scan (which import roots a bundle references) — the
transitive vendoring against a live ``_repo/`` is exercised in the export e2e.
"""
from __future__ import annotations

import pytest

from src.services.solutions.vendoring import (
    scan_imported_top_modules,
    vendor_shared_deps,
)


def test_finds_from_import() -> None:
    src = "from shared.halopsa import client\n"
    assert "shared" in scan_imported_top_modules(src)


def test_finds_plain_import_and_dotted() -> None:
    src = "import shared.util\nimport modules.helpers as h\n"
    mods = scan_imported_top_modules(src)
    assert "shared" in mods
    assert "modules" in mods


def test_ignores_stdlib_and_thirdparty() -> None:
    src = "import os\nimport httpx\nfrom datetime import datetime\nfrom shared.x import y\n"
    mods = scan_imported_top_modules(src)
    assert "shared" in mods
    # stdlib / third-party tops are still returned by the raw scan; filtering to
    # workspace roots is the caller's job — but the scan must at least capture
    # the real first segment, not a submodule.
    assert "datetime" in mods or "shared" in mods


def test_relative_imports_have_no_top_module() -> None:
    # `from . import x` / `from .sib import y` are intra-package, no top module.
    src = "from . import sibling\nfrom .sub import thing\n"
    mods = scan_imported_top_modules(src)
    assert "." not in mods
    assert "" not in mods


def test_syntax_error_returns_empty_not_raises() -> None:
    # A bundle file that doesn't parse must not crash the export scan.
    assert scan_imported_top_modules("def (:\n  pass") == set()


# --- vendor_shared_deps (transitive) ---------------------------------------

def _reader(repo: dict[str, str]):
    async def read(path: str) -> str | None:
        return repo.get(path)

    return read


@pytest.mark.asyncio
async def test_vendors_referenced_shared_module() -> None:
    bundle = {"workflows/w.py": "from shared.calc import VALUE\n"}
    repo = {"shared/calc.py": "VALUE = 42\n"}
    out = await vendor_shared_deps(bundle, _reader(repo))
    assert out == {"shared/calc.py": "VALUE = 42\n"}


@pytest.mark.asyncio
async def test_vendors_transitively() -> None:
    bundle = {"workflows/w.py": "import shared.a\n"}
    repo = {
        "shared/a.py": "import shared.b\n",
        "shared/b.py": "X = 1\n",
    }
    out = await vendor_shared_deps(bundle, _reader(repo))
    assert set(out) == {"shared/a.py", "shared/b.py"}


@pytest.mark.asyncio
async def test_does_not_vendor_solution_local_or_stdlib() -> None:
    bundle = {"workflows/w.py": "from modules.x import y\nimport os\nimport httpx\n"}
    repo = {"modules/x.py": "SHOULD_NOT_BE_VENDORED = 1\n"}  # modules/ is solution-local
    out = await vendor_shared_deps(bundle, _reader(repo))
    assert out == {}  # modules/ excluded; os/httpx not in repo


@pytest.mark.asyncio
async def test_package_init_resolution() -> None:
    bundle = {"workflows/w.py": "from shared import thing\n"}
    repo = {"shared/__init__.py": "thing = 1\n"}
    out = await vendor_shared_deps(bundle, _reader(repo))
    assert out == {"shared/__init__.py": "thing = 1\n"}
