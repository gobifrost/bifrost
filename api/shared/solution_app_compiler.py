"""Pure Vite compiler for standalone v2 application source.

The compiler accepts every build input explicitly so it can run in a scoped
compute worker without importing platform configuration, storage, or ORM code.
The caller is responsible for acquiring the SDK tarball and for publishing the
returned artifacts.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from bifrost.app_build_identity import build_identity
from bifrost.app_lockfile import ReproducibleDependencyBuildError, refresh_sdk_lockfile

SDK_TARBALL = "bifrost-sdk.tgz"
BUILD_STEP_TIMEOUT_S = 600
FAILURE_OUTPUT_LINES = 20


@dataclass(frozen=True)
class AppBuildInputs:
    """All source, SDK, identity, and process inputs for one app compile."""

    app_id: UUID | str
    source_files: Mapping[str, bytes]
    dependencies: Mapping[str, str]
    sdk_tarball: bytes
    recipe: dict[str, Any]
    environment: Mapping[str, str]
    require_lockfile: bool = False
    package_name: str = "bifrost-app"


@dataclass(frozen=True)
class AppBuildResult:
    """The compiler's in-memory artifacts and the identity they implement."""

    artifacts: dict[str, bytes]
    build_identity: str


def source_build_identity(inputs: AppBuildInputs) -> str:
    """Return the stable identity for the exact inputs this compiler receives."""
    return build_identity(
        inputs.app_id,
        inputs.source_files,
        inputs.dependencies,
        inputs.recipe,
        package_name=inputs.package_name,
    )


def materialize_app_source(
    workdir: Path,
    source_files: Mapping[str, bytes],
    dependencies: Mapping[str, str],
    sdk_tarball: bytes,
    *,
    package_name: str = "bifrost-app",
) -> None:
    """Write app source and its explicitly supplied local SDK package."""
    for rel, content in source_files.items():
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    (workdir / SDK_TARBALL).write_bytes(sdk_tarball)
    deps = {**dependencies, "bifrost": f"file:./{SDK_TARBALL}"}
    package = workdir / "package.json"
    if package.exists():
        existing = json.loads(package.read_text())
        existing.setdefault("dependencies", {})
        existing["dependencies"] = {**existing["dependencies"], **deps}
        package.write_text(json.dumps(existing, indent=2))
        return
    package.write_text(
        json.dumps(
            {"name": package_name, "private": True, "dependencies": deps},
            indent=2,
        )
    )


def run_vite_build(
    workdir: Path,
    *,
    base: str,
    require_lockfile: bool,
    environment: Mapping[str, str],
    timeout: int = BUILD_STEP_TIMEOUT_S,
) -> dict[str, bytes]:
    """Install dependencies, build with Vite, and collect ``dist/`` in memory."""
    locked = require_lockfile or (workdir / "package-lock.json").exists()
    if locked:
        refresh_sdk_lockfile(workdir, npm="npm", timeout=timeout, env=dict(environment))
    install_command = (
        ["npm", "ci", "--no-audit", "--no-fund"]
        if locked
        else ["npm", "install", "--no-audit", "--no-fund", "--package-lock=false"]
    )
    subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
        install_command,
        cwd=str(workdir),
        check=True,
        capture_output=True,
        timeout=timeout,
        env=dict(environment),
    )
    vite_command = ["npx", "vite"]
    if locked:
        locked_vite = workdir / "node_modules" / ".bin" / "vite"
        if not locked_vite.is_file():
            raise ReproducibleDependencyBuildError(
                "Locked builds require Vite in package-lock.json; add Vite to the app dependencies and update the lockfile."
            )
        vite_command = [str(locked_vite)]
    subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
        [*vite_command, "build", "--base", base],
        cwd=str(workdir),
        check=True,
        capture_output=True,
        timeout=timeout,
        env=dict(environment),
    )
    dist_dir = workdir / "dist"
    return {
        file.relative_to(dist_dir).as_posix(): file.read_bytes()
        for file in dist_dir.rglob("*")
        if file.is_file()
    }


def compile_app(inputs: AppBuildInputs) -> AppBuildResult:
    """Compile one app with no platform side effects beyond local subprocesses."""
    identity = source_build_identity(inputs)
    with tempfile.TemporaryDirectory(prefix=f"bifrost-appbuild-{inputs.app_id}-") as tmp:
        workdir = Path(tmp)
        materialize_app_source(
            workdir,
            inputs.source_files,
            inputs.dependencies,
            inputs.sdk_tarball,
            package_name=inputs.package_name,
        )
        artifacts = run_vite_build(
            workdir,
            base=f"/api/applications/{inputs.app_id}/dist/",
            require_lockfile=inputs.require_lockfile,
            environment=inputs.environment,
            timeout=BUILD_STEP_TIMEOUT_S,
        )
    return AppBuildResult(artifacts=artifacts, build_identity=identity)


def output_tail(exc: subprocess.CalledProcessError) -> str:
    """The diagnostic tail preserved by the deploy failure contract."""
    raw = exc.stderr or exc.output or b""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-FAILURE_OUTPUT_LINES:]) or "(no output)"
