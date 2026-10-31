"""The hosted App SDK is published before it is advertised or served."""
from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import Response


class _Body:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[tuple[str, bytes, str | None]] = []

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str | None = None, **_: object) -> None:
        self.objects[Key] = Body
        self.puts.append((Key, Body, ContentType))

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, _Body]:
        try:
            return {"Body": _Body(self.objects[Key])}
        except KeyError as exc:
            from src.core.s3_transport import S3NoSuchKey

            raise S3NoSuchKey("NoSuchKey", 404) from exc


class _Storage:
    def __init__(self, client: _Client) -> None:
        self._client = client
        self._settings = SimpleNamespace(
            s3_bucket="sdk-artifacts",
            s3_endpoint_url=None,
            s3_access_key="key",
            s3_secret_key="secret",
            s3_region="us-east-1",
        )

    def _get_client(self):
        client = self._client

        class _Context:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *_: object) -> None:
                return None

        return _Context()


def _artifact(content: bytes = b"factory bytes"):
    from src.services.sdk_package import HostedSdkArtifact

    fingerprint = hashlib.sha256(content).hexdigest()
    return HostedSdkArtifact(
        api_major=1,
        factory_version=1,
        factory_id="f" * 64,
        fingerprint=fingerprint,
        integrity="sha256-" + base64.b64encode(hashlib.sha256(content).digest()).decode(),
        content=content,
    )


@pytest.mark.asyncio
async def test_publish_then_read_uses_platform_prefix_and_exact_bytes() -> None:
    from src.services.sdk_package import HostedSdkArtifactStorage

    client = _Client()
    storage = HostedSdkArtifactStorage(settings=SimpleNamespace(s3_bucket="sdk-artifacts"))
    storage._get_client = _Storage(client)._get_client  # type: ignore[method-assign]
    artifact = _artifact()

    await storage.publish(artifact)

    assert client.puts == [
        (
            f"_platform/app-sdk/v1/{artifact.fingerprint}.js",
            artifact.content,
            "application/javascript; charset=utf-8",
        )
    ]
    assert await storage.read(artifact.fingerprint) == artifact.content


@pytest.mark.asyncio
async def test_read_rejects_tampered_or_missing_content() -> None:
    from src.services.sdk_package import HostedSdkArtifactStorage

    client = _Client()
    storage = HostedSdkArtifactStorage(settings=SimpleNamespace(s3_bucket="sdk-artifacts"))
    storage._get_client = _Storage(client)._get_client  # type: ignore[method-assign]
    artifact = _artifact()
    client.objects[f"_platform/app-sdk/v1/{artifact.fingerprint}.js"] = b"tampered"

    with pytest.raises(ValueError, match="integrity"):
        await storage.read(artifact.fingerprint)

    with pytest.raises(FileNotFoundError):
        await storage.read("0" * 64)


@pytest.mark.asyncio
async def test_startup_prewarms_binding_before_publishing_factory(monkeypatch) -> None:
    from src.services import sdk_package

    artifact = _artifact()
    calls: list[tuple[str, object]] = []

    def _fingerprint(version: str) -> str:
        calls.append(("fingerprint", version))
        return "binding-fingerprint"

    class _Storage:
        async def publish(self, published: object) -> None:
            calls.append(("publish", published))

    monkeypatch.setattr(sdk_package, "get_version", lambda: "v1.2.3")
    monkeypatch.setattr(sdk_package, "sdk_fingerprint", _fingerprint)
    monkeypatch.setattr(sdk_package, "current_hosted_sdk_artifact", lambda: artifact)
    monkeypatch.setattr(sdk_package, "HostedSdkArtifactStorage", _Storage)

    assert await sdk_package.publish_current_hosted_sdk_artifact() is artifact
    assert calls == [("fingerprint", "v1.2.3"), ("publish", artifact)]


def test_hosted_artifact_descriptor_is_content_addressed() -> None:
    from src.services.sdk_package import hosted_sdk_manifest

    artifact = _artifact(b"a static factory")
    manifest = hosted_sdk_manifest(artifact)

    assert manifest.api_major == 1
    assert manifest.factory_version == 1
    assert manifest.factory_id == artifact.factory_id
    assert manifest.fingerprint == artifact.fingerprint
    assert manifest.integrity == artifact.integrity
    assert manifest.asset_url == f"/api/app-sdk/assets/{artifact.fingerprint}.js"


def test_production_missing_artifact_fails_without_invoking_node(monkeypatch, tmp_path) -> None:
    """A production image must never compile the hosted factory at startup."""
    from src.services import sdk_package

    missing_artifact = tmp_path / "hosted-v1.js"
    monkeypatch.setattr(sdk_package, "_HOSTED_ARTIFACT_PATH", missing_artifact)
    monkeypatch.delenv("BIFROST_ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        sdk_package,
        "_build_hosted_factory",
        lambda _: pytest.fail("production startup must not invoke Node"),
    )
    sdk_package.current_hosted_sdk_artifact.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="Missing pre-built"):
            sdk_package.current_hosted_sdk_artifact()
    finally:
        sdk_package.current_hosted_sdk_artifact.cache_clear()


@pytest.mark.asyncio
async def test_asset_endpoint_uses_immutable_nosniff_headers(monkeypatch) -> None:
    from src.routers import app_sdk_runtime

    artifact = _artifact()

    async def _read(fingerprint: str) -> bytes:
        assert fingerprint == artifact.fingerprint
        return artifact.content

    monkeypatch.setattr(app_sdk_runtime, "read_hosted_sdk_asset", _read)
    response = await app_sdk_runtime.get_asset(artifact.fingerprint)

    assert response.body == artifact.content
    assert response.media_type == "application/javascript"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_manifest_endpoint_is_not_cached(monkeypatch) -> None:
    from src.routers import app_sdk_runtime
    from src.services.sdk_package import hosted_sdk_manifest

    artifact = _artifact()
    monkeypatch.setattr(app_sdk_runtime, "hosted_sdk_manifest", lambda: hosted_sdk_manifest(artifact))
    response = Response()

    manifest = await app_sdk_runtime.get_manifest(response)

    assert manifest.fingerprint == artifact.fingerprint
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_asset_endpoint_rejects_malformed_or_missing_artifacts(monkeypatch) -> None:
    from src.routers import app_sdk_runtime

    with pytest.raises(HTTPException) as malformed:
        await app_sdk_runtime.get_asset("not-a-hash")
    assert malformed.value.status_code == 404

    async def _missing(_: str) -> bytes:
        raise FileNotFoundError

    monkeypatch.setattr(app_sdk_runtime, "read_hosted_sdk_asset", _missing)
    with pytest.raises(HTTPException) as missing:
        await app_sdk_runtime.get_asset("a" * 64)
    assert missing.value.status_code == 404
