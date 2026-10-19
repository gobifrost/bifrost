"""Worker-published runtime import roots used by conversion analysis."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.runtime_modules import read_runtime_modules
from shared.subprocess_env import package_tool_env
from src.services.execution.process_pool import (
    ProcessPoolManager,
    _MODULE_ROOT_PROBE,
    _get_installed_module_roots,
)


class _FakeRedis:
    def __init__(self, hashes: dict[str, str]) -> None:
        self.hashes = hashes

    async def scan(self, cursor, match=None, count=None):
        assert match == "bifrost:pool:*"
        return 0, list(self.hashes)

    async def hget(self, key, field):
        assert field == "module_roots"
        return self.hashes.get(key)


class _FakeCM:
    def __init__(self, redis) -> None:
        self.redis = redis

    async def __aenter__(self):
        return self.redis

    async def __aexit__(self, *args):
        return False


def test_module_root_subprocess_reads_user_site_without_executing_pth(
    monkeypatch,
) -> None:
    """The fresh probe includes a newly-created user site as metadata only."""
    completed = MagicMock(returncode=0, stdout='["alpha", "beta"]', stderr="")
    tool_env = {"PATH": "/usr/bin", "HOME": "/home/app"}
    monkeypatch.setattr("src.services.execution.process_pool.package_tool_env", lambda: tool_env)
    run = MagicMock(return_value=completed)
    monkeypatch.setattr("src.services.execution.process_pool.subprocess.run", run)

    assert _get_installed_module_roots() == {"status": "available", "roots": ["alpha", "beta"]}

    args, kwargs = run.call_args
    assert args[0][0:3] == [sys.executable, "-S", "-c"]
    probe = args[0][3]
    assert "site.getusersitepackages()" in probe
    assert "site.getsitepackages()" in probe
    assert "importlib.metadata" in probe
    assert "addsitedir" not in probe
    assert kwargs["env"] == tool_env


def test_module_root_probe_reads_package_metadata_without_executing_pth(
    tmp_path: Path,
) -> None:
    """A user-site package without top_level.txt is found without running its .pth."""
    user_base = tmp_path / "user-base"
    user_site = (
        user_base
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    package = user_site / "demo_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    dist_info = user_site / "demo_package-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: demo-package\nVersion: 1.0\n")
    namespace = user_site / "contoso_namespace"
    namespace.mkdir()
    (namespace / "tools.py").write_text("")
    (dist_info / "RECORD").write_text(
        "demo_package/__init__.py,,\ncontoso_namespace/tools.py,,\n"
    )
    sentinel = tmp_path / "pth-executed"
    (user_site / "sentinel.pth").write_text(
        f"import pathlib; pathlib.Path({str(sentinel)!r}).touch()\n"
    )
    env = package_tool_env()
    env["PYTHONUSERBASE"] = str(user_base)

    result = subprocess.run(
        [sys.executable, "-S", "-c", _MODULE_ROOT_PROBE],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0
    assert "demo_package" in json.loads(result.stdout)
    assert "contoso_namespace" in json.loads(result.stdout)
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_registration_and_package_update_publish_module_root_metadata(monkeypatch) -> None:
    """Registration and install refresh write the same worker-scoped metadata."""
    pool = ProcessPoolManager()
    pool.worker_id = "worker-1"
    pool._started = True
    redis = AsyncMock()
    pool._get_redis = AsyncMock(return_value=redis)
    monkeypatch.setattr(
        "src.services.execution.process_pool._get_installed_packages",
        lambda: [{"name": "demo", "version": "1.0"}],
    )
    monkeypatch.setattr(
        "src.services.execution.process_pool._get_installed_module_roots",
        lambda: {"status": "available", "roots": ["demo"]},
    )

    await pool._register_worker()
    mapping = redis.hset.await_args.kwargs["mapping"]
    assert json.loads(mapping["module_roots"]) == {"status": "available", "roots": ["demo"]}

    await pool.update_packages()
    assert redis.hset.await_args_list[-1].args == ("bifrost:pool:worker-1",)
    assert json.loads(redis.hset.await_args_list[-1].kwargs["mapping"]["module_roots"]) == {
        "status": "available",
        "roots": ["demo"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_first", [False, True])
async def test_reader_merges_live_workers_and_marks_partial_metadata_unavailable(
    monkeypatch, missing_first: bool,
) -> None:
    """Any registered worker without valid metadata makes completeness unavailable."""
    redis = _FakeRedis(
        {
            "bifrost:pool:worker-1": json.dumps(
                {"status": "available", "roots": ["alpha", "beta"]}
            ),
            "bifrost:pool:worker-2": json.dumps(
                {"status": "available", "roots": ["beta", "gamma"]}
            ),
            "bifrost:pool:worker-3": json.dumps(
                {"status": "unavailable", "roots": []}
            ),
            "bifrost:pool:worker-1:heartbeat": json.dumps(
                {"status": "available", "roots": ["must_not_appear"]}
            ),
        }
    )
    if missing_first:
        redis.hashes = dict(reversed(list(redis.hashes.items())))
    monkeypatch.setattr("src.core.cache.get_redis", lambda: _FakeCM(redis))

    runtime = await read_runtime_modules()

    assert runtime.roots == frozenset({"alpha", "beta", "gamma"})
    assert runtime.available is False


@pytest.mark.asyncio
async def test_reader_marks_inventory_unavailable_when_worker_scan_exceeds_bound(monkeypatch) -> None:
    """A large registration set is never treated as a complete inventory."""
    redis = _FakeRedis(
        {
            f"bifrost:pool:worker-{index}": json.dumps(
                {"status": "available", "roots": ["demo"]}
            )
            for index in range(33)
        }
    )
    monkeypatch.setattr("src.core.cache.get_redis", lambda: _FakeCM(redis))

    runtime = await read_runtime_modules()

    assert runtime.available is False
    assert runtime.roots == frozenset()
