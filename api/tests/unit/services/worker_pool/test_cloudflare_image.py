"""Bounded OCI copy contract for the Cloudflare worker image publisher."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterable
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from src.services.worker_pool.cloudflare_image import (
    CHUNK_BYTES,
    CloudflareImageCopier,
    CloudflareImageError,
    _HttpsRedirect,
)


API_TOKEN = "cloudflare-api-token-that-must-not-leak"
GHCR_TOKEN = "ghcr-token-that-must-not-leak"
ACCOUNT_ID = "a" * 32
REPOSITORY = "bifrost-worker"
VERSION = "1.2.3"


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


class Response:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None, *, blob: bool = False) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = io.BytesIO(body)
        self._blob = blob
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        self.read_sizes.append(amount)
        if self._blob:
            assert amount == CHUNK_BYTES
        return self._body.read(amount)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class RegistryOpener:
    """A tiny registry that records request ordering and consumes upload streams."""

    def __init__(self, *, layer: bytes = b"layer" * 50_000, version: str = VERSION, existing: Iterable[str] = ()) -> None:
        self.calls: list[Request] = []
        self.uploads: dict[str, bytes] = {}
        self.manifests: list[Request] = []
        self.existing = set(existing)
        self.config = _json({
            "architecture": "amd64", "os": "linux", "config": {"Env": [f"BIFROST_VERSION={version}"]},
        })
        self.config_digest = _digest(self.config)
        self.layer = layer
        self.layer_digest = _digest(layer)
        self.manifest = _json({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": self.config_digest, "size": len(self.config)},
            "layers": [{"digest": self.layer_digest, "size": len(layer)}],
        })
        self.manifest_digest = _digest(self.manifest)
        self.index = _json({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{
                "digest": self.manifest_digest, "size": len(self.manifest),
                "platform": {"os": "linux", "architecture": "amd64"},
            }],
        })
        self.index_digest = _digest(self.index)

    @property
    def source(self) -> str:
        return f"ghcr.io/gobifrost/bifrost-api@{self.index_digest}"

    def open(self, request: Request, timeout: int) -> Response:
        assert timeout == 60
        self.calls.append(request)
        url = request.full_url
        method = request.get_method()
        if url == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/containers/registries/registry.cloudflare.com/credentials":
            assert method == "POST"
            assert request.headers["Authorization"] == f"Bearer {API_TOKEN}"
            return Response(200, _json({"success": True, "result": {
                "registry_host": "registry.cloudflare.com", "username": "user", "password": "password",
            }}))
        if url.startswith("https://ghcr.io/token?"):
            assert method == "GET"
            return Response(200, _json({"token": GHCR_TOKEN}))
        source_base = "https://ghcr.io/v2/gobifrost/bifrost-api"
        if url == source_base + "/manifests/" + self.index_digest:
            return Response(200, self.index)
        if url == source_base + "/manifests/" + self.manifest_digest:
            return Response(200, self.manifest)
        if url == source_base + "/blobs/" + self.config_digest:
            return Response(200, self.config)
        if url == source_base + "/blobs/" + self.layer_digest:
            return Response(200, self.layer, blob=True)
        destination = f"https://registry.cloudflare.com/v2/{ACCOUNT_ID}/{REPOSITORY}"
        if url.startswith(destination + "/blobs/") and method == "HEAD":
            digest = url.rsplit("/", 1)[1]
            if digest in self.existing:
                return Response(200)
            raise HTTPError(url, 404, "missing", Message(), io.BytesIO())
        if url == destination + "/blobs/uploads/" and method == "POST":
            return Response(202, headers={"Location": "/v2/" + ACCOUNT_ID + "/" + REPOSITORY + "/blobs/uploads/session"})
        if url.startswith(destination + "/blobs/uploads/session?") and method == "PUT":
            data = request.data
            assert data is not None
            assert not isinstance(data, (bytes, bytearray))
            uploaded = b"".join(data)
            digest = parse_qs(urlsplit(url).query)["digest"][0]
            self.uploads[digest] = uploaded
            return Response(201)
        if url == destination + "/manifests/" + self.manifest_digest and method == "PUT":
            self.manifests.append(request)
            return Response(201, headers={"Location": url})
        raise AssertionError(f"unexpected OCI request {method} {url}")


def _copy(opener: RegistryOpener, **overrides: object):
    return CloudflareImageCopier(opener=opener).copy(
        source=overrides.get("source", opener.source), expected_version=overrides.get("expected_version", VERSION),
        account_id=ACCOUNT_ID, repository=REPOSITORY, api_token=API_TOKEN,
    )


def test_copy_selects_linux_amd64_from_an_index_streams_layers_and_publishes_manifest_last() -> None:
    opener = RegistryOpener()

    copied = _copy(opener)

    assert copied.reference == f"registry.cloudflare.com/{ACCOUNT_ID}/{REPOSITORY}@{opener.manifest_digest}"
    assert copied.digest == opener.manifest_digest
    assert opener.uploads == {opener.config_digest: opener.config, opener.layer_digest: opener.layer}
    assert len(opener.manifests) == 1
    assert opener.calls[-1] is opener.manifests[0]


def test_copy_resumes_when_every_destination_blob_already_exists() -> None:
    opener = RegistryOpener(existing=())
    opener.existing.update({opener.config_digest, opener.layer_digest})

    _copy(opener)

    assert opener.uploads == {}
    assert len(opener.manifests) == 1


def test_bad_layer_digest_prevents_manifest_publication() -> None:
    opener = RegistryOpener(layer=b"wrong-layer")
    bad_digest = "sha256:" + "0" * 64
    opener.manifest = _json({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": opener.config_digest, "size": len(opener.config)},
        "layers": [{"digest": bad_digest, "size": len(opener.layer)}],
    })
    opener.layer_digest = bad_digest
    opener.manifest_digest = _digest(opener.manifest)
    opener.index = _json({
        "schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": opener.manifest_digest, "size": len(opener.manifest), "platform": {"os": "linux", "architecture": "amd64"}}],
    })
    opener.index_digest = _digest(opener.index)

    with pytest.raises(CloudflareImageError, match="digest"):
        _copy(opener)
    assert opener.manifests == []


def test_version_mismatch_stops_before_any_destination_blob_or_manifest_write() -> None:
    opener = RegistryOpener(version="other-version")

    with pytest.raises(CloudflareImageError, match="does not match"):
        _copy(opener)
    assert not any("registry.cloudflare.com/v2/" in request.full_url for request in opener.calls)


def test_existing_pinned_cloudflare_image_is_verified_without_copying_it_again() -> None:
    class CloudflareSourceOpener(RegistryOpener):
        def open(self, request: Request, timeout: int) -> Response:
            source_base = f"https://registry.cloudflare.com/v2/{ACCOUNT_ID}/owned-worker"
            if request.full_url == source_base + "/manifests/" + self.manifest_digest:
                self.calls.append(request)
                return Response(200, self.manifest)
            if request.full_url == source_base + "/blobs/" + self.config_digest:
                self.calls.append(request)
                return Response(200, self.config)
            return super().open(request, timeout)

    opener = CloudflareSourceOpener()
    copied = _copy(
        opener,
        source=f"registry.cloudflare.com/{ACCOUNT_ID}/owned-worker@{opener.manifest_digest}",
    )

    assert copied.reference == f"registry.cloudflare.com/{ACCOUNT_ID}/owned-worker@{opener.manifest_digest}"
    assert opener.uploads == {}
    assert opener.manifests == []
    assert not any(f"/v2/{ACCOUNT_ID}/{REPOSITORY}/" in request.full_url for request in opener.calls)


@pytest.mark.parametrize("target", ["http://storage.example/blob", "https://user@storage.example/blob"])
def test_redirects_reject_insecure_or_credentialed_targets(target: str) -> None:
    request = Request("https://registry.cloudflare.com/v2/repository/blobs/sha256:" + "a" * 64, headers={"Authorization": "Basic secret"})
    with pytest.raises(CloudflareImageError, match="invalid redirect"):
        _HttpsRedirect().redirect_request(request, None, 302, "redirect", Message(), target)


def test_cross_origin_https_redirect_strips_authorization() -> None:
    request = Request("https://registry.cloudflare.com/v2/repository/blobs/sha256:" + "a" * 64, headers={"Authorization": "Basic secret"})

    redirected = _HttpsRedirect().redirect_request(
        request, None, 302, "redirect", Message(), "https://signed.example/blob?sig=private",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_registry_failures_never_expose_tokens_or_signed_urls() -> None:
    class FailingOpener:
        def open(self, request: Request, timeout: int) -> Response:
            raise HTTPError(
                "https://api.cloudflare.com/request?sig=signed-url-token", 403, API_TOKEN, Message(), io.BytesIO(),
            )

    with pytest.raises(CloudflareImageError) as caught:
        CloudflareImageCopier(opener=FailingOpener()).copy(
            source="ghcr.io/gobifrost/bifrost-api@sha256:" + "a" * 64,
            expected_version=VERSION, account_id=ACCOUNT_ID, repository=REPOSITORY, api_token=API_TOKEN,
        )
    assert API_TOKEN not in str(caught.value)
    assert "signed-url-token" not in str(caught.value)
