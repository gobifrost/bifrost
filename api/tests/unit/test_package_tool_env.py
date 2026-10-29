"""pip / npm / npx subprocesses run with an allowlisted environment.

Package installs and app builds execute third-party code, so they get
``shared.subprocess_env.package_tool_env()`` instead of the platform's full
environment.
"""

from __future__ import annotations

import ast
import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from shared.subprocess_env import package_tool_env
from src.config import Settings

API_ROOT = Path(__file__).resolve().parents[2]

_TOOL_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/home/app",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LC_CTYPE": "C.UTF-8",
    "TMPDIR": "/tmp",
    "TZ": "UTC",
    "HTTP_PROXY": "http://proxy.example:3128",
    "HTTPS_PROXY": "http://proxy.example:3128",
    "NO_PROXY": "localhost",
    "http_proxy": "http://proxy.example:3128",
    "https_proxy": "http://proxy.example:3128",
    "no_proxy": "localhost",
    "SSL_CERT_FILE": "/etc/ssl/certs/ca.pem",
    "SSL_CERT_DIR": "/etc/ssl/certs",
    "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca.pem",
    "PIP_CERT": "/etc/ssl/certs/ca.pem",
    "PIP_INDEX_URL": "https://index.example/simple",
    "PIP_NO_CACHE_DIR": "1",
    "NODE_EXTRA_CA_CERTS": "/etc/ssl/certs/ca.pem",
    "NODE_OPTIONS": "--max-old-space-size=2048",
    "npm_config_registry": "https://registry.example/",
    "NPM_CONFIG_CACHE": "/tmp/npm-cache",
    "PYTHONPATH": "/app",
    "PYTHONUNBUFFERED": "1",
    "VIRTUAL_ENV": "/opt/venv",
}


def _credential_names() -> list[str]:
    """Every settings variable plus the credential names the deployments set."""
    names = [f"BIFROST_{name.upper()}" for name in Settings.model_fields]
    names += [
        field.validation_alias
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    ]
    names += [
        "BIFROST_ACCESS_TOKEN",
        "BIFROST_REFRESH_TOKEN",
        "BIFROST_VERSION",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "DB_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "S3_SECRET_KEY",
        "REDIS_URL",
        "RABBITMQ_DEFAULT_USER",
        "RABBITMQ_DEFAULT_PASS",
        "NB_SETUP_KEY",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "NODE_AUTH_TOKEN",
        "NPM_CONFIG__AUTHTOKEN",
        "npm_config__authToken",
        "PIP_PASSWORD",
        "PYTHON_SECRET",
    ]
    return names


def test_helper_keeps_tool_settings_and_drops_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = _credential_names()
    assert "BIFROST_SECRET_KEY" in credentials
    assert "BIFROST_DATABASE_URL" in credentials
    assert "BIFROST_S3_SECRET_KEY" in credentials
    assert "ANTHROPIC_API_KEY" in credentials

    monkeypatch.setattr(os, "environ", {**_TOOL_ENV, **{name: "value" for name in credentials}})

    env = package_tool_env()

    assert env == _TOOL_ENV


# --- every call site passes the helper's environment --------------------------


class _FakeStream:
    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration


class _FakeProcess:
    returncode = 0

    def __init__(self) -> None:
        self.stdout = _FakeStream()

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return b"[]", b""

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        return None


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the keyword arguments of every subprocess started."""
    calls: list[dict[str, Any]] = []

    def fake_run(
        *popenargs: Any,
        input: Any = None,
        capture_output: bool = False,
        timeout: float | None = None,
        check: bool = False,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(popenargs[0], 0, stdout="[]", stderr="")

    async def fake_exec(
        program: Any,
        *args: Any,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
        limit: int = 2**16,
        **kwds: Any,
    ) -> _FakeProcess:
        calls.append(kwds)
        return _FakeProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("BIFROST_SECRET_KEY", "platform-secret-value-for-unit-tests-only")
    monkeypatch.setenv("PIP_INDEX_URL", "https://index.example/simple")
    return calls


async def _simple_worker_pip_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.execution.simple_worker import _pip_install

    _pip_install(["requests"])


async def _setup_helper_pip_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.execution.requirements_setup_helper import _get_installed_packages

    _get_installed_packages()


async def _process_pool_pip_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.execution.process_pool import _get_installed_packages

    _get_installed_packages()


async def _app_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.solutions.app_build import SolutionAppBuilder

    (tmp_path / "dist").mkdir()
    SolutionAppBuilder()._run_vite_build(tmp_path)


async def _router_outdated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routers.packages import check_package_updates

    await check_package_updates()


async def _router_pip_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routers.packages import get_installed_packages_local

    await get_installed_packages_local()


async def _consumer_uninstall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.jobs.consumers.package_install import PackageInstallConsumer

    await PackageInstallConsumer()._pip_uninstall("requests")


async def _consumer_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.jobs.consumers.package_install import PackageInstallConsumer

    await PackageInstallConsumer()._pip_install("requests", None)


async def _consumer_install_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.core.requirements_cache as requirements_cache
    from src.jobs.consumers.package_install import PackageInstallConsumer

    async def get_requirements() -> dict[str, Any] | None:
        return {"content": "requests\n"}

    monkeypatch.setattr(requirements_cache, "get_requirements", get_requirements)
    await PackageInstallConsumer()._pip_install_requirements()


async def _manager_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.package_manager import WorkspacePackageManager

    await WorkspacePackageManager(tmp_path).list_installed_packages()


async def _manager_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.package_manager import PackageInfo, WorkspacePackageManager

    manager = WorkspacePackageManager(tmp_path)

    async def get_package_info(package_name: str) -> PackageInfo:
        return PackageInfo(name=package_name, version="1.0.0", summary="")

    monkeypatch.setattr(manager, "get_package_info", get_package_info)
    await manager.install_package("requests", append_to_requirements=False)


async def _manager_install_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.package_manager import WorkspacePackageManager

    requirements = tmp_path / "requirements.txt"
    requirements.write_text("requests\n")
    await WorkspacePackageManager(tmp_path).install_requirements_streaming(requirements)


_CALL_SITES: dict[str, tuple[Callable[[Path, pytest.MonkeyPatch], Awaitable[None]], int]] = {
    "simple_worker._pip_install": (_simple_worker_pip_install, 1),
    "requirements_setup_helper._get_installed_packages": (_setup_helper_pip_list, 1),
    "process_pool._get_installed_packages": (_process_pool_pip_list, 1),
    "app_build._run_vite_build": (_app_build, 2),
    "packages.check_package_updates": (_router_outdated, 1),
    "packages.get_installed_packages_local": (_router_pip_list, 1),
    "package_install._pip_uninstall": (_consumer_uninstall, 1),
    "package_install._pip_install": (_consumer_install, 1),
    "package_install._pip_install_requirements": (_consumer_install_requirements, 1),
    "package_manager.list_installed_packages": (_manager_list, 1),
    "package_manager.install_package": (_manager_install, 2),
    "package_manager.install_requirements_streaming": (_manager_install_requirements, 1),
}


@pytest.mark.parametrize("site", sorted(_CALL_SITES))
async def test_call_site_passes_package_tool_env(
    site: str,
    recorded: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, expected_calls = _CALL_SITES[site]
    await call(tmp_path, monkeypatch)

    expected_env = package_tool_env()
    envs = [kwargs.get("env") for kwargs in recorded]

    assert "BIFROST_SECRET_KEY" not in expected_env
    assert expected_env["PIP_INDEX_URL"] == "https://index.example/simple"
    assert envs == [expected_env] * expected_calls


# --- guard: new pip / npm / npx / uv subprocesses must use the helper ---------

_PACKAGE_TOOLS = {"pip", "npm", "npx", "uv"}
_SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}


def _argv_literals(node: ast.Call) -> list[str]:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return []
    if func.attr == "create_subprocess_exec":
        parts: list[ast.expr] = list(node.args)
    elif node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
        parts = list(node.args[0].elts)
    else:
        parts = []
    return [p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str)]


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return False
    if func.value.id == "subprocess":
        return func.attr in _SUBPROCESS_FUNCS
    return func.value.id == "asyncio" and func.attr == "create_subprocess_exec"


def _uses_package_tool_env(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "env" or not isinstance(keyword.value, ast.Call):
            continue
        target = keyword.value.func
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
        if name == "package_tool_env":
            return True
    return False


def test_package_tool_subprocesses_use_package_tool_env() -> None:
    found: list[str] = []
    offenders: list[str] = []
    for directory in ("src", "shared"):
        for path in sorted((API_ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
                    continue
                if not _PACKAGE_TOOLS.intersection(_argv_literals(node)):
                    continue
                location = f"{path.relative_to(API_ROOT)}:{node.lineno}"
                found.append(location)
                if not _uses_package_tool_env(node):
                    offenders.append(location)

    found_files = {location.rsplit(":", 1)[0] for location in found}
    assert {
        "src/services/execution/simple_worker.py",
        "src/services/solutions/app_build.py",
        "src/jobs/consumers/package_install.py",
    } <= found_files
    assert offenders == [], (
        "pip / npm / npx / uv subprocesses must pass env=package_tool_env(): "
        f"{offenders}"
    )
