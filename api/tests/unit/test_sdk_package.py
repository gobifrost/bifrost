"""/api/sdk/download builds an installable `bifrost` package (Codex P1-a).

A standalone_v2 app resolves `import { BifrostProvider, useWorkflow } from
"bifrost"` from the instance. This test exercises the real esbuild bundle of the
SDK source and asserts the produced npm tarball has the right shape: a
`package/package.json` named `bifrost` with React peer deps + a `dist/index.mjs`
bundle that exports the v2 surface and keeps React external.

The SDK source is copied into `sdk_package/sdk_src/` in the api image (Dockerfile).
When running before that image layer exists (host/older container), this test
stages the same files from the client tree so it still validates the bundler.
"""
from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

_SDK_SERVICE = Path("/app/src/services/sdk_package")
_SRC_FILES = [
    "provider.tsx",
    "tables.ts",
    "transport.ts",
    "use-table.ts",
    "use-infinite-table.ts",
    "ws-client.ts",
    "use-workflow.ts",
    "use-workflow-hooks.ts",
    "files.ts",
    "use-files.ts",
    "bifrost-header.tsx",
]


def _ensure_sdk_src() -> bool:
    """Make sure sdk_src/ holds the SDK source + index.ts barrel. Returns False
    if neither the image copy nor the client tree is available (skip)."""
    dst = _SDK_SERVICE / "sdk_src"
    if (dst / "index.ts").is_file():
        return True
    # stage from the client tree (mirrors the Dockerfile COPY)
    candidates = [
        Path("/app").parent / "client" / "src" / "lib" / "app-sdk",
        Path(__file__).resolve().parents[3] / "client" / "src" / "lib" / "app-sdk",
    ]
    client = next((c for c in candidates if (c / "index.v2.ts").is_file()), None)
    if client is None:
        return False
    dst.mkdir(parents=True, exist_ok=True)
    for f in _SRC_FILES:
        shutil.copy(client / f, dst / f)
    shutil.copy(client / "index.v2.ts", dst / "index.ts")
    return True


@pytest.mark.e2e
def test_build_sdk_tarball_cached_per_version(monkeypatch):
    """The tarball is a pure function of version + baked-in SDK source, and
    /api/sdk/download + every app deploy call it — so the esbuild subprocess
    must run ONCE per version, not per request."""
    import src.services.sdk_package as sdkpkg

    sdkpkg.build_sdk_tarball.cache_clear()
    calls: list[Path] = []

    def _fake_bundle(workdir: Path) -> bytes:
        calls.append(workdir)
        return b"//bundle"

    monkeypatch.setattr(sdkpkg, "_bundle", _fake_bundle)
    try:
        first = sdkpkg.build_sdk_tarball("v9.9.9")
        second = sdkpkg.build_sdk_tarball("v9.9.9")
    finally:
        # Don't leak the fake-bundle tarball into other tests via the cache.
        sdkpkg.build_sdk_tarball.cache_clear()

    assert first == second
    assert len(calls) == 1, "builder ran more than once for the same version"


@pytest.mark.e2e
def test_build_sdk_tarball_shape_and_exports():
    if not _ensure_sdk_src():
        pytest.skip("SDK source not available (no image copy, no client tree)")
    # esbuild must be installed (app_bundler node_modules) — skip if not present.
    if not (_SDK_SERVICE.parent / "app_bundler" / "node_modules" / "esbuild").exists():
        pytest.skip("esbuild not installed in this environment")

    import src.services.sdk_package as sdkpkg

    sdkpkg.build_sdk_tarball.cache_clear()  # never serve another test's stubbed bundle
    data = sdkpkg.build_sdk_tarball("v1.2-3-gabc1234")
    assert data[:2] == b"\x1f\x8b", "not a gzip tarball"

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
        assert "package/package.json" in names
        assert "package/dist/index.mjs" in names

        pkg_file = tar.extractfile("package/package.json")
        assert pkg_file is not None
        pkg = json.loads(pkg_file.read())
        assert pkg["name"] == "bifrost"
        # git-describe "v1.2-3-gabc1234" has no patch component → coerced to 1.2.0.
        assert pkg["version"] == "1.2.0"
        # No runtime deps — the SDK is fetch + React + lucide (all peers) only.
        assert "dependencies" not in pkg or not pkg["dependencies"]
        assert "react" in pkg["peerDependencies"]
        assert "lucide-react" in pkg["peerDependencies"]

        bundle_file = tar.extractfile("package/dist/index.mjs")
        assert bundle_file is not None
        bundle = bundle_file.read().decode()
        for sym in (
            "BifrostProvider", "useWorkflow", "useWorkflowQuery",
            "useWorkflowMutation", "useTable", "tables", "BifrostHeader",
        ):
            assert sym in bundle, f"{sym} missing from bundle"
        # React + lucide stay external (imported, not inlined).
        assert 'from "react"' in bundle
        assert 'from "lucide-react"' in bundle
