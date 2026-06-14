"""C1 regression: install_zip MUST invalidate the Redis config cache after it
applies config values.

``ConfigRepository.set_config`` writes the DB row but does NOT touch the Redis
config cache — invalidation lives in the config router, in ``delete_solution``,
and in deploy's reattach. Before the fix, ``install_zip`` applied config values
via ``_apply_config_values`` and NEVER invalidated, so ``merged_for_sdk`` kept
serving the OLD cached value (for a SECRET, the old ciphertext) until TTL —
workflows ran against stale config right after a "successful" install.

These tests drive the real ``install_zip`` (e2e stack, like the other
``@pytest.mark.e2e`` DB-backed unit tests) and SPY on ``invalidate_all_config``:

* applied a config value  → invalidate_all_config called once with the org scope
* applied NO config value → invalidate_all_config NOT called (nothing changed)
"""
from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest

from src.services.solutions import zip_install


def _zip_with_config_decl(slug: str, *, with_decl: bool) -> bytes:
    """Build an in-memory Solution workspace zip.

    ``with_decl=True`` declares a single string config key (API_URL) so a
    form-supplied value for it is applied on install. ``with_decl=False`` is a
    descriptor + workflow only — no config surface at all.
    """
    files: dict[str, str] = {
        "bifrost.solution.yaml": f"slug: {slug}\nname: {slug.upper()}\nscope: global\n",
        ".bifrost/workflows.yaml": (
            "workflows:\n"
            "  11111111-1111-1111-1111-111111111111:\n"
            "    id: 11111111-1111-1111-1111-111111111111\n"
            "    name: main\n"
            "    function_name: run\n"
            "    path: workflows/main.py\n"
        ),
        "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
    }
    if with_decl:
        files[".bifrost/configs.yaml"] = (
            "configs:\n"
            "  API_URL:\n"
            "    id: 33333333-3333-3333-3333-333333333333\n"
            "    key: API_URL\n"
            "    type: string\n"
            "    required: false\n"
            "    description: endpoint\n"
            "    position: 0\n"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


@pytest.mark.e2e
async def test_install_zip_invalidates_config_cache_iff_config_applied(
    db_session, monkeypatch
) -> None:
    """Both cases in ONE test (one event loop) to avoid tearing down the shared
    async redis client between function-scoped loops:

    * install that applies a config value → invalidate_all_config(org) called once
    * install that applies NO config value → invalidate_all_config NOT called
    """
    calls: list[str | None] = []

    async def _spy(org: str | None) -> None:
        calls.append(org)

    # install_zip imports invalidate_all_config from src.core.cache locally, so
    # patch it at the source module.
    monkeypatch.setattr("src.core.cache.invalidate_all_config", _spy)

    # Case 1: a config value is applied → cache invalidated for the org scope.
    await zip_install.install_zip(
        db_session,
        _zip_with_config_decl(f"c1cache-{uuid4().hex[:8]}", with_decl=True),
        organization_id=None,  # global scope → invalidate(None)
        config_values={"API_URL": "https://example.test"},
        deployer_email="test@example.com",
    )
    assert calls == [None], (
        f"expected exactly one invalidate_all_config(None) after applying a "
        f"config value, got {calls}"
    )

    # Case 2: nothing config-related applied → no invalidation (nothing changed).
    calls.clear()
    await zip_install.install_zip(
        db_session,
        _zip_with_config_decl(f"c1nocache-{uuid4().hex[:8]}", with_decl=False),
        organization_id=None,
        config_values={},
        deployer_email="test@example.com",
    )
    assert calls == [], (
        f"expected no invalidate_all_config calls when no config applied, got {calls}"
    )
