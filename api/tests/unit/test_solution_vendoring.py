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

from src.services.solutions.vendoring import vendor_shared_deps


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


@pytest.mark.asyncio
async def test_from_namespace_pkg_import_submodule() -> None:
    """`from shared import calc` where shared is a PEP-420 namespace (no
    __init__.py) must vendor shared/calc.py, not just look for shared/__init__.py
    (Codex Sub-plan 4 P2)."""
    bundle = {"workflows/w.py": "from shared import calc\n"}
    repo = {"shared/calc.py": "VALUE = 1\n"}  # no shared/__init__.py
    out = await vendor_shared_deps(bundle, _reader(repo))
    assert out == {"shared/calc.py": "VALUE = 1\n"}


async def test_vendor_repo_reader_distinguishes_absent_from_failed() -> None:
    """404 = legitimately absent (stdlib/third-party import) — NOT a failure.
    Any other non-200 must be recorded so deploy can refuse to ship a bundle
    with silently omitted shared modules (issue #465)."""
    from bifrost.commands.solution import _vendor_repo_reader

    class _Resp:
        def __init__(self, status_code, content=None):
            self.status_code = status_code
            self._content = content

        def json(self):
            return {"content": self._content}

    class _FakeClient:
        async def post(self, path, json=None, **kwargs):
            target = json["path"]
            if target == "shared/ok.py":
                return _Resp(200, "OK_SOURCE")
            if target == "shared/absent.py":
                return _Resp(404)
            return _Resp(503)

    failures: dict[str, str] = {}
    read = _vendor_repo_reader(_FakeClient(), failures)

    assert await read("shared/ok.py") == "OK_SOURCE"
    assert await read("shared/absent.py") is None
    assert await read("shared/flaky.py") is None
    assert failures == {"shared/flaky.py": "HTTP 503"}
