"""Import boundaries for virtual import module-cache contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[3]


def _run_import_probe(source: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_virtual_import_does_not_load_async_module_cache_or_storage_stack() -> None:
    result = _run_import_probe(
        """
        import json
        import sys

        import src.services.execution.virtual_import  # noqa: F401

        blocked = sorted(
            name for name in sys.modules
            if name == "src.core.module_cache"
            or name == "src.services.repo_storage"
            or name.startswith("aiobotocore")
            or name.startswith("botocore")
        )

        print(json.dumps({"blocked": blocked}))
        """
    )

    assert result["blocked"] == []


def test_sync_and_async_module_cache_exports_share_canonical_contract() -> None:
    result = _run_import_probe(
        """
        import json

        from src.core import module_cache, module_cache_contract, module_cache_sync

        print(json.dumps({
            "async_cached_module_is_canonical": (
                module_cache.CachedModule is module_cache_contract.CachedModule
            ),
            "sync_cached_module_is_canonical": (
                module_cache_sync.CachedModule is module_cache_contract.CachedModule
            ),
            "async_key_helper_is_canonical": (
                module_cache.module_resolution_cache_key
                is module_cache_contract.module_resolution_cache_key
            ),
            "sync_key_helper_is_canonical": (
                module_cache_sync.module_resolution_cache_key
                is module_cache_contract.module_resolution_cache_key
            ),
            "async_prefix_is_canonical": (
                module_cache.MODULE_RESOLUTION_KEY_PREFIX
                is module_cache_contract.MODULE_RESOLUTION_KEY_PREFIX
            ),
            "sync_prefix_is_canonical": (
                module_cache_sync.MODULE_RESOLUTION_KEY_PREFIX
                is module_cache_contract.MODULE_RESOLUTION_KEY_PREFIX
            ),
            "sample_key": module_cache_contract.module_resolution_cache_key(
                "shared.tools",
                solution_id="solution-1",
                global_repo_access=True,
            ),
        }))
        """
    )

    assert result == {
        "async_cached_module_is_canonical": True,
        "sync_cached_module_is_canonical": True,
        "async_key_helper_is_canonical": True,
        "sync_key_helper_is_canonical": True,
        "async_prefix_is_canonical": True,
        "sync_prefix_is_canonical": True,
        "sample_key": "solution-1:1:shared.tools",
    }
