"""Build the installable ``bifrost`` web SDK package served by /api/sdk/download.

A ``standalone_v2`` Solution app declares ``"bifrost"`` as a dependency and
resolves it from the instance (``npm install`` against /api/sdk/download), so the
SAME mechanism works on a developer laptop (``npm run dev``) and in the platform's
server-side build. This module produces the npm-installable tarball on the fly,
version-stamped to the running instance — directly analogous to the CLI's
``/api/cli/download`` (a Python tarball).

The SDK source (provider, tables, hooks) lives in ``client/src/lib/app-sdk`` and
is copied into the api image at ``sdk_src/`` (see Dockerfile). It is bundled with
esbuild into one ESM file with ``react``/``react-dom`` kept EXTERNAL (peer deps —
the consuming app provides them so React stays a singleton).
"""
from __future__ import annotations

import functools
import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SDK_SRC = _HERE / "sdk_src"
_BUILDER = _HERE / "build_sdk.js"
# esbuild is installed under the app_bundler package (shared Node toolchain).
_NODE_MODULES = _HERE.parent / "app_bundler" / "node_modules"

# The peer deps a v2 app must already have for the SDK to resolve at runtime.
# React (hooks) + lucide-react (BifrostHeader icons). The SDK uses plain fetch +
# useState for data — no data-fetching library.
_PEER_DEPS = {
    "react": ">=18",
    "react-dom": ">=18",
    "lucide-react": ">=0.400",
}


def _pep440ish(version: str) -> str:
    """npm semver is stricter than git-describe output. Coerce ``v0.6-219-gabc``
    / ``...-dirty`` into a valid-enough ``0.6.0`` so ``npm install`` accepts it.
    Falls back to ``0.0.0`` when the version is unparseable (e.g. ``unknown``)."""
    import re

    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not m:
        return "0.0.0"
    major, minor, patch = m.group(1), m.group(2), m.group(3) or "0"
    return f"{major}.{minor}.{patch}"


def _bundle(workdir: Path) -> bytes:
    """Run esbuild over the SDK source, returning the bundled ESM bytes."""
    out = workdir / "index.mjs"
    subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
        ["node", str(_BUILDER), str(_SDK_SRC), str(out)],
        cwd=str(workdir),
        check=True,
        capture_output=True,
        env={"NODE_PATH": str(_NODE_MODULES), "PATH": "/usr/bin:/usr/local/bin:/bin"},
        # Same wedge class app_build guards against: this runs under the
        # install write lock during app deploys; a hung node must not hold it.
        timeout=120,
    )
    return out.read_bytes()


# Caching is safe: the tarball is a pure function of version + the SDK source
# baked into the image. maxsize=2 covers a rolling-upgrade window.
@functools.lru_cache(maxsize=2)
def build_sdk_tarball(version: str) -> bytes:
    """Produce an npm-installable ``bifrost`` package tarball (gzip), version
    stamped. Layout: ``package/package.json`` (name ``bifrost``, ESM ``module``
    entry, React peer deps) + ``package/dist/index.mjs`` (the bundle)."""
    pkg_version = _pep440ish(version)

    with tempfile.TemporaryDirectory(prefix="bifrost-sdk-build-") as tmp:
        workdir = Path(tmp)
        bundle = _bundle(workdir)

        package_json = {
            "name": "bifrost",
            "version": pkg_version,
            "description": "Bifrost web SDK for standalone v2 apps.",
            "type": "module",
            "module": "dist/index.mjs",
            "main": "dist/index.mjs",
            "exports": {".": {"import": "./dist/index.mjs"}},
            "peerDependencies": _PEER_DEPS,
        }

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            def _add(name: str, data: bytes) -> None:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, fileobj=io.BytesIO(data))

            # npm expects everything under a top-level "package/" dir.
            _add("package/package.json", json.dumps(package_json, indent=2).encode())
            _add("package/dist/index.mjs", bundle)

        return buffer.getvalue()
