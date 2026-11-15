"""Bounded OCI image transfer without a Docker daemon or credential-bearing logs.

The canonical API-owned job runs this synchronous I/O in its runner subprocess.
urllib does not emit httpx's request-URL logs for signed blob redirects. Every
cross-origin redirect drops authorization, and only HTTPS is accepted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, overload
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from shared.models import SEMVER_PATTERN

CHUNK_BYTES = 64 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024**3
MAX_LAYER_BYTES = 2 * 1024**3
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)


class CloudflareImageError(RuntimeError):
    """Curated image-transfer error, never an HTTP body or signed URL."""


class _HttpsRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.username or target.password:
            raise CloudflareImageError("Worker registry returned an invalid redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != target.netloc:
            redirected.remove_header("Authorization")
        return redirected


@dataclass(frozen=True)
class CopiedWorkerImage:
    reference: str
    digest: str
    source_digest: str
    worker_version: str


class CloudflareImageCopier:
    """Copy one verified linux/amd64 image, streaming one layer at a time."""

    def __init__(self, *, opener=None, progress: Callable[[str], None] | None = None) -> None:
        self._opener = opener if opener is not None else build_opener(_HttpsRedirect())
        self._progress = progress

    @overload
    def _open(self, request: Request, *, missing_ok: Literal[False] = False) -> Any: ...

    @overload
    def _open(self, request: Request, *, missing_ok: Literal[True]) -> Any | None: ...

    def _open(self, request: Request, *, missing_ok: bool = False) -> Any | None:
        try:
            response = self._opener.open(request, timeout=60)
        except HTTPError as error:
            if missing_ok and error.code == 404:
                error.close()
                return None
            error.close()
            raise CloudflareImageError("Worker registry request was rejected") from None
        except Exception:
            raise CloudflareImageError("Worker registry request failed") from None
        if response.status not in (200, 201, 202, 204):
            response.close()
            raise CloudflareImageError("Worker registry returned an invalid status")
        return response

    def _document(self, url: str, headers: dict[str, str]) -> tuple[bytes, Any]:
        with self._open(Request(url, headers=headers)) as response:
            body = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(body) > MAX_DOCUMENT_BYTES:
            raise CloudflareImageError("Worker image metadata is too large")
        try:
            return body, json.loads(body)
        except (ValueError, TypeError):
            raise CloudflareImageError("Worker registry returned invalid image metadata") from None

    @staticmethod
    def _descriptor(value: Any, *, limit: int) -> tuple[str, int]:
        if (not isinstance(value, dict) or not isinstance(value.get("digest"), str)
                or not DIGEST.fullmatch(value["digest"]) or type(value.get("size")) is not int
                or not 0 <= value["size"] <= limit):
            raise CloudflareImageError("Worker image has an invalid content descriptor")
        return value["digest"], value["size"]

    @staticmethod
    def _verify(body: bytes, expected: str) -> None:
        if "sha256:" + hashlib.sha256(body).hexdigest() != expected:
            raise CloudflareImageError("Worker image content digest did not match")

    @staticmethod
    def _location(response, origin: str) -> str:
        location = response.headers.get("Location")
        if not location:
            raise CloudflareImageError("Worker registry did not identify its upload")
        location = urljoin(origin, location)
        parsed = urlsplit(location)
        if parsed.scheme != "https" or parsed.netloc != urlsplit(origin).netloc or parsed.username or parsed.password:
            raise CloudflareImageError("Worker registry returned an invalid upload location")
        return location

    def copy(
        self, *, source: str | None, expected_version: str,
        account_id: str, repository: str, api_token: str,
    ) -> CopiedWorkerImage:
        """Resolve, verify, and copy; failures resume by digest on the next job.

        Only the built-in public GHCR source may resolve a tag. An explicit
        deployment image must already be pinned; its embedded version must
        exactly equal this API's version. There is no alternate-image fallback.
        """
        if not re.fullmatch(r"[0-9a-f]{32}", account_id) or not REPOSITORY.fullmatch(repository):
            raise CloudflareImageError("Invalid Cloudflare image destination")
        if source is None:
            version = expected_version.removeprefix("v")
            if "-dev." in version:
                source = "ghcr.io/gobifrost/bifrost-api:dev"
            elif re.fullmatch(SEMVER_PATTERN, version) and "+" not in version:
                source = f"ghcr.io/gobifrost/bifrost-api:{version}"
            else:
                raise CloudflareImageError("Publish this API's worker image before connecting Cloudflare")
        elif "@sha256:" not in source:
            raise CloudflareImageError("The deployment worker image must be pinned by digest")
        if "@" in source:
            image_path, reference = source.rsplit("@", 1)
            if not DIGEST.fullmatch(reference):
                raise CloudflareImageError("Invalid worker image digest")
        else:
            image_path, reference = source.rsplit(":", 1)
        host, source_repository = image_path.split("/", 1)
        if host not in ("ghcr.io", "registry.cloudflare.com") or not REPOSITORY.fullmatch(source_repository):
            raise CloudflareImageError("Use the published Bifrost image or a pinned Cloudflare image")
        if host == "registry.cloudflare.com" and not source_repository.startswith(account_id + "/"):
            raise CloudflareImageError("The Cloudflare worker image belongs to another account")

        # Credentials remain only inside this API-owned process. Never log
        # their request/response, source config environments, or redirect URLs.
        credentials_request = Request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/containers/registries/registry.cloudflare.com/credentials",
            method="POST", headers={"Authorization": "Bearer " + api_token, "Content-Type": "application/json"},
            data=json.dumps({"expiration_minutes": 15, "permissions": ["push", "pull"]}).encode(),
        )
        with self._open(credentials_request) as response:
            raw = response.read(MAX_DOCUMENT_BYTES + 1)
        try:
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise ValueError
            envelope = json.loads(raw)
            credential = envelope["result"]
            if envelope.get("success") is not True or credential["registry_host"] != "registry.cloudflare.com":
                raise ValueError
            user, password = credential["username"], credential["password"]
            if not isinstance(user, str) or not isinstance(password, str):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise CloudflareImageError("Cloudflare registry credentials were unavailable") from None
        destination_headers = {"Authorization": "Basic " + base64.b64encode((user + ":" + password).encode()).decode()}
        source_headers = dict(destination_headers)
        if host == "ghcr.io":
            _, auth = self._document(
                "https://ghcr.io/token?" + urlencode({"service": "ghcr.io", "scope": f"repository:{source_repository}:pull"}), {},
            )
            if not isinstance(auth, dict) or not isinstance(auth.get("token"), str):
                raise CloudflareImageError("The published worker image is not accessible")
            source_headers = {"Authorization": "Bearer " + auth["token"]}
        source_base = f"https://{host}/v2/{source_repository}"
        manifest_headers = {**source_headers, "Accept": ", ".join(MANIFEST_TYPES)}
        body, manifest = self._document(source_base + "/manifests/" + reference, manifest_headers)
        source_digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if reference.startswith("sha256:"):
            self._verify(body, reference)
        if not isinstance(manifest, dict):
            raise CloudflareImageError("Worker image manifest is invalid")
        if "manifests" in manifest:
            if not isinstance(manifest["manifests"], list):
                raise CloudflareImageError("Worker image index is invalid")
            candidates = [item for item in manifest["manifests"] if isinstance(item, dict)
                          and isinstance(item.get("platform"), dict)
                          and item["platform"].get("os") == "linux"
                          and item.get("platform", {}).get("architecture") == "amd64"]
            if len(candidates) != 1:
                raise CloudflareImageError("Worker image must identify exactly one linux/amd64 image")
            digest, _ = self._descriptor(candidates[0], limit=MAX_DOCUMENT_BYTES)
            body, manifest = self._document(source_base + "/manifests/" + digest, manifest_headers)
            self._verify(body, digest)
        if not isinstance(manifest, dict) or manifest.get("mediaType") not in MANIFEST_TYPES[1::2]:
            raise CloudflareImageError("Worker image manifest type is unsupported")
        config_digest, config_size = self._descriptor(manifest.get("config"), limit=MAX_DOCUMENT_BYTES)
        config_body, config = self._document(source_base + "/blobs/" + config_digest, source_headers)
        self._verify(config_body, config_digest)
        if len(config_body) != config_size or not isinstance(config, dict):
            raise CloudflareImageError("Worker image configuration is invalid")
        if config.get("architecture") != "amd64" or config.get("os") != "linux":
            raise CloudflareImageError("Worker image must run on linux/amd64")
        runtime_config = config.get("config")
        if not isinstance(runtime_config, dict) or not isinstance(runtime_config.get("Env"), list):
            raise CloudflareImageError("Worker image has no embedded API version")
        versions = [item[len("BIFROST_VERSION="):] for item in runtime_config["Env"]
                    if isinstance(item, str) and item.startswith("BIFROST_VERSION=")]
        if versions != [expected_version]:
            raise CloudflareImageError("The published worker image does not match this API version")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or len(layers) > 128:
            raise CloudflareImageError("Worker image has too many layers")
        descriptors = [(config_digest, config_size), *[
            self._descriptor(layer, limit=MAX_LAYER_BYTES) for layer in layers
        ]]
        if sum(size for _, size in descriptors) > MAX_IMAGE_BYTES:
            raise CloudflareImageError("Worker image exceeds the supported transfer size")
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if host == "registry.cloudflare.com":
            return CopiedWorkerImage(f"{host}/{source_repository}@{digest}", digest, source_digest, expected_version)

        destination = f"https://registry.cloudflare.com/v2/{account_id}/{repository}"
        for blob_digest, size in descriptors:
            existing = self._open(Request(destination + "/blobs/" + blob_digest, method="HEAD", headers=destination_headers), missing_ok=True)
            if existing is not None:
                existing.close()
                continue
            if self._progress is not None:
                self._progress(blob_digest)
            with self._open(Request(destination + "/blobs/uploads/", method="POST", data=b"", headers=destination_headers)) as response:
                upload = self._location(response, destination)
            hasher = hashlib.sha256()
            count = 0
            with self._open(Request(source_base + "/blobs/" + blob_digest, headers=source_headers)) as download:
                def chunks():
                    nonlocal count
                    while chunk := download.read(CHUNK_BYTES):
                        count += len(chunk)
                        if count > size:
                            raise CloudflareImageError("Worker image layer exceeds its declared size")
                        hasher.update(chunk)
                        yield chunk
                separator = "&" if urlsplit(upload).query else "?"
                request = Request(upload + separator + urlencode({"digest": blob_digest}), method="PUT", data=chunks(), headers={
                    **destination_headers, "Content-Type": "application/octet-stream", "Content-Length": str(size),
                })
                with self._open(request):
                    pass
            if count != size or "sha256:" + hasher.hexdigest() != blob_digest:
                raise CloudflareImageError("Worker image layer digest did not match")
        with self._open(Request(destination + "/manifests/" + digest, method="PUT", data=body, headers={
            **destination_headers, "Content-Type": manifest["mediaType"],
        })):
            pass
        return CopiedWorkerImage(f"registry.cloudflare.com/{account_id}/{repository}@{digest}", digest, source_digest, expected_version)
