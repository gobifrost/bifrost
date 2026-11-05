"""Build and publish the browser App SDK package and hosted factory.

A ``standalone_v2`` Solution app imports ``"bifrost"`` while the CLI installs it
transiently from the selected instance (``npm install`` against /api/sdk/download), so the
SAME mechanism works on a developer laptop (``npm run dev``) and in the platform's
server-side build. This module produces the npm-installable tarball on the fly,
version-stamped to the running instance — directly analogous to the CLI's
``/api/cli/download/bifrost-cli.tar.gz`` (a Python tarball).

The binding is installed into each App while the full SDK implementation is a
static factory image artifact published to shared platform storage at startup.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from shared.models import HostedAppSdkDescriptor
from shared.version import get_version
from src.config import Settings, get_settings
from src.core.s3_transport import S3NoSuchKey, S3Transport

_HERE = Path(__file__).resolve().parent
_SDK_SRC = _HERE / "sdk_src"
_BUILDER = _HERE / "build_sdk.js"
_TYPE_BUILDER = _HERE / "build_types.js"
# The hosted factory also bundles lucide, so use this package's complete build
# toolchain instead of the app bundler's esbuild-only dependency directory.
_NODE_MODULES = _HERE / "node_modules"

# The binding shares the App's React singleton.  The hosted factory contains its
# own icon implementation, so Apps never need to chase its icon dependencies.
_PEER_DEPS = {
    "react": ">=18",
    "react-dom": ">=18",
}
_HOSTED_ARTIFACT_PATH = Path(
    os.environ.get("BIFROST_HOSTED_APP_SDK_ARTIFACT", "/app/artifacts/app-sdk/hosted-v1.js")
)
_PLATFORM_PREFIX = "_platform/app-sdk/v1"
_HOSTED_API_MAJOR = 1
_HOSTED_FACTORY_VERSION = 1


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


def sdk_package_version(version: str) -> str:
    """Return the exact npm package version stamped into SDK tarballs."""
    return _pep440ish(version)


def _bundle(workdir: Path) -> bytes:
    """Run esbuild over the installed binding, returning ESM bytes."""
    out = workdir / "index.mjs"
    subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
        ["node", str(_BUILDER), "binding", str(_SDK_SRC), str(out)],
        cwd=str(workdir),
        check=True,
        capture_output=True,
        env={"NODE_PATH": str(_NODE_MODULES), "PATH": "/usr/bin:/usr/local/bin:/bin"},
        # Same wedge class app_build guards against: this runs under the
        # install write lock during app deploys; a hung node must not hold it.
        timeout=120,
    )
    return out.read_bytes()


def _build_hosted_factory(out: Path) -> None:
    """Build the complete SDK factory for development images only."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
        ["node", str(_BUILDER), "hosted", str(_SDK_SRC), str(out)],
        cwd=str(out.parent),
        check=True,
        capture_output=True,
        env={"NODE_PATH": str(_NODE_MODULES), "PATH": "/usr/bin:/usr/local/bin:/bin"},
        timeout=120,
    )


def _build_types(workdir: Path) -> Path:
    """Emit declarations from source instead of maintaining a hand-written API."""
    out = workdir / "types"
    subprocess.run(  # noqa: S603 - trusted toolchain, fixed argv
        ["node", str(_TYPE_BUILDER), str(_SDK_SRC), str(out)],
        cwd=str(workdir),
        check=True,
        capture_output=True,
        env={"NODE_PATH": str(_NODE_MODULES), "PATH": "/usr/bin:/usr/local/bin:/bin"},
        timeout=120,
    )
    return out


# Caching is safe: the binding is a pure function of its source.  It is
# deliberately separate from the hosted factory, so full SDK implementation
# changes do not claim that an App's installed binding changed.
@functools.lru_cache(maxsize=2)
def _built_bundle(version: str) -> bytes:
    """esbuild output for the SDK source baked into this image. Cached: pure
    function of the source; ``version`` keys the cache for rolling upgrades."""
    with tempfile.TemporaryDirectory(prefix="bifrost-sdk-build-") as tmp:
        return _bundle(Path(tmp))


def sdk_fingerprint(version: str) -> str:
    """Content fingerprint of the installed binding (sha256, 16 hex chars).

    Pure function of the binding source: changes exactly when the binding
    changes. This is what ``bifrost solution start`` compares against an
    app's installed copy — no manual bump anywhere. Raises on a build
    failure (e.g. broken node toolchain); callers that need a
    never-fails read (``/api/version``) must catch and degrade — see
    ``get_sdk_fingerprint`` in ``src.routers.version``. Not caught here:
    ``_built_bundle`` is lru_cached, and functools does not cache raised
    exceptions, so a transient failure is retried on the next call rather
    than permanently poisoning the cache.
    """
    return hashlib.sha256(_built_bundle(version)).hexdigest()[:16]


@functools.lru_cache(maxsize=1)
def sdk_contract_version() -> int:
    """The SDK<->server wire contract version (see ``sdk-contract.json``).

    Bumped only on a DECIDED breaking change to the standalone app SDK's wire
    surface (content fingerprint = automatic, contract version = manual).
    The JSON file is baked into the image alongside the SDK source (see
    Dockerfile), so this resolves identically in dev and in the built image.
    """
    contract = json.loads((_SDK_SRC / "sdk-contract.json").read_text())
    return contract["version"]


@functools.lru_cache(maxsize=1)
def sdk_automatic_upgrade_contracts() -> frozenset[int]:
    """Reviewed source contracts that can rebuild without developer changes.

    A wire-contract mismatch is insufficient evidence of source compatibility.
    Each target release explicitly lists the predecessors it can upgrade.
    """
    contract = json.loads((_SDK_SRC / "sdk-contract.json").read_text())
    predecessors = contract.get("automatic_upgrade_from", [])
    if not isinstance(predecessors, list) or any(
        type(version) is not int or not 0 < version < contract["version"]
        for version in predecessors
    ):
        raise ValueError("Invalid SDK automatic-upgrade contract declaration")
    return frozenset(predecessors)


def sdk_binding_api_major() -> int:
    """The installed binding interface supported by this API image."""
    return _HOSTED_API_MAJOR


def sdk_supported_binding_api_majors() -> frozenset[int]:
    """Binding majors this image can advertise.  Only v1 exists today."""
    return frozenset({_HOSTED_API_MAJOR})


@functools.lru_cache(maxsize=1)
def sdk_automatic_hosted_migration_contracts() -> frozenset[int]:
    """Reviewed wire contracts whose bundles can migrate to the v1 binding."""
    contract = json.loads((_SDK_SRC / "sdk-contract.json").read_text())
    predecessors = contract.get("automatic_hosted_migration_from", [])
    if not isinstance(predecessors, list) or any(
        type(version) is not int or not 0 < version <= contract["version"]
        for version in predecessors
    ):
        raise ValueError("Invalid hosted SDK migration contract declaration")
    return frozenset(predecessors)


# Caching is safe: the tarball is a pure function of version + the SDK source
# baked into the image. maxsize=2 covers a rolling-upgrade window.
@functools.lru_cache(maxsize=2)
def build_sdk_tarball(version: str) -> bytes:
    """Produce an npm-installable ``bifrost`` package tarball (gzip), version
    stamped. Layout: ``package/package.json`` (name ``bifrost``, ESM ``module``
    entry, React peer deps) + ``package/dist/index.mjs`` (the bundle)."""
    pkg_version = sdk_package_version(version)
    bundle = _built_bundle(version)

    package_json = {
        "name": "bifrost",
        "version": pkg_version,
        "description": "Bifrost web SDK for standalone v2 apps.",
        "type": "module",
        "module": "dist/index.mjs",
        "main": "dist/index.mjs",
        "types": "types/hosted-binding.d.ts",
        "exports": {".": {"types": "./types/hosted-binding.d.ts", "import": "./dist/index.mjs"}},
        "peerDependencies": _PEER_DEPS,
        "bifrost": {
            "fingerprint": sdk_fingerprint(version),
            "contract": sdk_contract_version(),
            "binding_api_major": sdk_binding_api_major(),
        },
    }

    with tempfile.TemporaryDirectory(prefix="bifrost-sdk-types-") as tmp:
        types_root = _build_types(Path(tmp))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            def _add(name: str, data: bytes) -> None:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, fileobj=io.BytesIO(data))

            # npm expects everything under a top-level "package/" dir.
            _add("package/package.json", json.dumps(package_json, indent=2).encode())
            _add("package/dist/index.mjs", bundle)
            for declaration in types_root.rglob("*.d.ts"):
                _add(
                    f"package/types/{declaration.relative_to(types_root).as_posix()}",
                    declaration.read_bytes(),
                )
        return buffer.getvalue()


@dataclass(frozen=True)
class HostedSdkArtifact:
    """A generated static factory plus its independently verifiable identity."""

    api_major: int
    factory_version: int
    factory_id: str
    fingerprint: str
    integrity: str
    content: bytes


def _read_hosted_artifact(path: Path) -> HostedSdkArtifact:
    """Read the pre-built image artifact and reject stale or tampered metadata."""
    metadata_path = path.with_suffix(path.suffix + ".json")
    content = path.read_bytes()
    metadata = json.loads(metadata_path.read_text())
    digest = hashlib.sha256(content).digest()
    fingerprint = digest.hex()
    integrity = "sha256-" + base64.b64encode(digest).decode()
    if (
        metadata != {
            "api_major": _HOSTED_API_MAJOR,
            "factory_version": _HOSTED_FACTORY_VERSION,
            "factory_id": metadata.get("factory_id"),
            "fingerprint": fingerprint,
            "integrity": integrity,
        }
        or not isinstance(metadata["factory_id"], str)
        or len(metadata["factory_id"]) != 64
        or any(char not in "0123456789abcdef" for char in metadata["factory_id"])
    ):
        raise ValueError("Invalid hosted App SDK artifact metadata")
    return HostedSdkArtifact(
        api_major=_HOSTED_API_MAJOR,
        factory_version=_HOSTED_FACTORY_VERSION,
        factory_id=metadata["factory_id"],
        fingerprint=fingerprint,
        integrity=integrity,
        content=content,
    )


@functools.lru_cache(maxsize=1)
def current_hosted_sdk_artifact() -> HostedSdkArtifact:
    """Return this image's factory, building it once only for development."""
    if os.environ.get("BIFROST_ENVIRONMENT") == "development":
        # Dev images bind-mount API source and keep the static production
        # artifact out of the source tree.  This still occurs before serving
        # requests and is never triggered by opening an App.
        dev_path = Path("/tmp/bifrost/app-sdk/hosted-v1.js")
        _build_hosted_factory(dev_path)
        return _read_hosted_artifact(dev_path)
    if not _HOSTED_ARTIFACT_PATH.is_file():
        raise RuntimeError(
            "Missing pre-built hosted App SDK artifact; rebuild the API image before startup"
        )
    return _read_hosted_artifact(_HOSTED_ARTIFACT_PATH)


class HostedSdkArtifactStorage:
    """Shared, platform-owned storage for immutable browser SDK artifacts."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._bucket = self._settings.s3_bucket or ""

    @asynccontextmanager
    async def _get_client(self):
        async with S3Transport(
            endpoint_url=self._settings.s3_endpoint_url,
            access_key_id=self._settings.s3_access_key,
            secret_access_key=self._settings.s3_secret_key,
            region=self._settings.s3_region,
        ) as client:
            yield client

    @staticmethod
    def _key(fingerprint: str) -> str:
        return f"{_PLATFORM_PREFIX}/{fingerprint}.js"

    async def publish(self, artifact: HostedSdkArtifact) -> None:
        """Write the exact immutable bytes before any replica advertises them."""
        if not self._bucket:
            raise RuntimeError("Hosted App SDK requires configured shared object storage")
        async with self._get_client() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=self._key(artifact.fingerprint),
                Body=artifact.content,
                ContentType="application/javascript; charset=utf-8",
            )

    async def read(self, fingerprint: str) -> bytes:
        """Return only bytes whose content hash exactly matches the requested key."""
        try:
            async with self._get_client() as client:
                response = await client.get_object(Bucket=self._bucket, Key=self._key(fingerprint))
                content = await response["Body"].read()
        except S3NoSuchKey as exc:
            raise FileNotFoundError(f"Hosted App SDK artifact not found: {fingerprint}") from exc
        if hashlib.sha256(content).hexdigest() != fingerprint:
            raise ValueError("Hosted App SDK artifact integrity check failed")
        return content


def hosted_sdk_manifest(artifact: HostedSdkArtifact | None = None) -> HostedAppSdkDescriptor:
    """Describe the current image factory without exposing any tenant data."""
    artifact = artifact or current_hosted_sdk_artifact()
    return HostedAppSdkDescriptor(
        api_major=artifact.api_major,
        factory_version=artifact.factory_version,
        factory_id=artifact.factory_id,
        fingerprint=artifact.fingerprint,
        integrity=artifact.integrity,
        asset_url=f"/api/app-sdk/assets/{artifact.fingerprint}.js",
    )


async def publish_current_hosted_sdk_artifact() -> HostedSdkArtifact:
    """Prewarm the binding and publish its factory before API readiness."""
    # App metadata reads the binding fingerprint.  Build it on the startup
    # path so opening the first App can use the cached bytes without invoking
    # Node, and so a broken binding fails readiness instead of user traffic.
    await asyncio.to_thread(sdk_fingerprint, get_version())
    artifact = current_hosted_sdk_artifact()
    await HostedSdkArtifactStorage().publish(artifact)
    return artifact


async def read_hosted_sdk_asset(fingerprint: str) -> bytes:
    """Read a retained immutable artifact by content hash."""
    return await HostedSdkArtifactStorage().read(fingerprint)
