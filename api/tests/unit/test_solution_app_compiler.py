"""Unit coverage for the dependency-free standalone-app compiler."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from bifrost.app_build_identity import build_identity
from shared import solution_app_compiler as compiler
from shared.solution_app_compiler import (
    AppBuildInputs,
    BUILD_STEP_TIMEOUT_S,
    compile_app,
    materialize_app_source,
)


RECIPE = {
    "schema": 1,
    "variant": "server-install-v2",
    "sdk": "a" * 64,
    "toolchain": "b" * 64,
    "environment": "c" * 64,
}


def test_materialize_uses_supplied_sdk_tarball_and_preserves_package_metadata(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "example", "scripts": {"build": "vite"}, "dependencies": {"react": "^18"}})
    )

    materialize_app_source(
        tmp_path,
        {"src/main.tsx": b"export {};"},
        {"lodash": "^4"},
        b"explicit-sdk-tarball",
    )

    package = json.loads((tmp_path / "package.json").read_text())
    assert (tmp_path / "bifrost-sdk.tgz").read_bytes() == b"explicit-sdk-tarball"
    assert (tmp_path / "src/main.tsx").read_bytes() == b"export {};"
    assert package["scripts"] == {"build": "vite"}
    assert package["dependencies"] == {
        "react": "^18",
        "lodash": "^4",
        "bifrost": "file:./bifrost-sdk.tgz",
    }


def test_compile_returns_artifacts_and_identity_from_explicit_inputs(
    monkeypatch,
) -> None:
    inputs = AppBuildInputs(
        app_id="app-1400",
        source_files={"index.html": b"<main />"},
        dependencies={"vite": "^6"},
        sdk_tarball=b"sdk-from-caller",
        recipe=RECIPE,
        environment={"PATH": "/toolchain"},
    )
    observed_materialize: list[
        tuple[Path, Mapping[str, bytes], Mapping[str, str], bytes]
    ] = []
    observed_vite: list[tuple[Path, dict[str, object]]] = []

    def materialize(
        workdir: Path,
        source_files: Mapping[str, bytes],
        dependencies: Mapping[str, str],
        sdk_tarball: bytes,
        *, package_name: str,
    ) -> None:
        assert package_name == inputs.package_name
        observed_materialize.append((workdir, source_files, dependencies, sdk_tarball))

    def vite_build(workdir: Path, **kwargs: object) -> dict[str, bytes]:
        observed_vite.append((workdir, kwargs))
        return {"index.html": b"built", "assets/app.js": b"console.log(1)"}

    monkeypatch.setattr(compiler, "materialize_app_source", materialize)
    monkeypatch.setattr(compiler, "run_vite_build", vite_build)

    result = compile_app(inputs)

    assert result.artifacts == {"index.html": b"built", "assets/app.js": b"console.log(1)"}
    assert result.build_identity == build_identity(
        "app-1400", inputs.source_files, inputs.dependencies, RECIPE
    )
    _workdir, source_files, dependencies, sdk_tarball = observed_materialize[0]
    assert source_files is inputs.source_files
    assert dependencies is inputs.dependencies
    assert sdk_tarball == b"sdk-from-caller"
    _vite_workdir, vite_kwargs = observed_vite[0]
    assert vite_kwargs == {
        "base": "/api/applications/app-1400/dist/",
        "require_lockfile": False,
        "environment": {"PATH": "/toolchain"},
        "timeout": BUILD_STEP_TIMEOUT_S,
    }
