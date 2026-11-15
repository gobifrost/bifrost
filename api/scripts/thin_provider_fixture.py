"""Local provider fixture for the platform-job integration suite.

The fixture retains the scheduler suite's HTTP OAuth, embeddings, and MCP
responses while adding a local TLS video provider. The container-only DNS alias
preserves the production OpenRouter hostname checks; a generated short-lived
certificate preserves TLS verification. No external provider request or real
credential is involved.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts import scheduler_fixture_server

VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_CAPACITY_SCOPE = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")


@dataclass
class CapacityFixture:
    """One test-preseeded external capacity resource, never a launcher."""

    scope: str
    token: str
    worker_id: UUID
    resource_uid: str
    worker_version: str
    memory_limit_bytes: int
    deleted: bool = False

    def profile(self) -> dict[str, object]:
        return {
            "protocol_version": 1,
            "scope": self.scope,
            "worker_version": self.worker_version,
            "memory_limit_bytes": self.memory_limit_bytes,
            "max_workers": 1,
            "max_lifetime_seconds": 21600,
        }

    def resource(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "worker_id": str(self.worker_id),
            "worker_version": self.worker_version,
            "resource_uid": self.resource_uid,
            "state": "deleted" if self.deleted else "running",
            "out_of_memory": False,
        }


_capacity_lock = threading.Lock()
_capacity_fixtures: dict[str, CapacityFixture] = {}


class VideoHandler(scheduler_fixture_server.FixtureHandler):
    def _capacity_fixture(
        self, scope: str, *, require_scope: bool
    ) -> CapacityFixture | None:
        with _capacity_lock:
            fixture = _capacity_fixtures.get(scope)
        if fixture is None:
            self._json(404, {"error": "capacity_fixture_not_found"})
            return None
        if self.headers.get("Authorization") != f"Bearer {fixture.token}":
            self._json(401, {"error": "unauthorized"})
            return None
        if require_scope and self.headers.get("X-Bifrost-Capacity-Scope") != scope:
            self._json(400, {"error": "invalid_scope"})
            return None
        return fixture

    def _configure_capacity_fixture(self) -> None:
        try:
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            scope = payload["scope"]
            token = payload["token"]
            worker_id = UUID(payload["worker_id"])
            resource_uid = payload["resource_uid"]
            worker_version = payload["worker_version"]
            memory_limit_bytes = payload["memory_limit_bytes"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid_capacity_fixture"})
            return
        if (
            not isinstance(scope, str)
            or _CAPACITY_SCOPE.fullmatch(scope) is None
            or not isinstance(token, str)
            or len(token) < 32
            or not token.isascii()
            or not isinstance(resource_uid, str)
            or _CAPACITY_SCOPE.fullmatch(resource_uid) is None
            or not isinstance(worker_version, str)
            or not worker_version
            or len(worker_version) > 100
            or not isinstance(memory_limit_bytes, int)
            or isinstance(memory_limit_bytes, bool)
            or memory_limit_bytes < 64 * 1024 * 1024
            or memory_limit_bytes % (1024 * 1024)
        ):
            self._json(400, {"error": "invalid_capacity_fixture"})
            return
        if self.headers.get("Authorization") != f"Bearer {token}":
            self._json(401, {"error": "unauthorized"})
            return
        with _capacity_lock:
            if scope in _capacity_fixtures:
                self._json(409, {"error": "capacity_fixture_exists"})
                return
            _capacity_fixtures[scope] = CapacityFixture(
                scope=scope,
                token=token,
                worker_id=worker_id,
                resource_uid=resource_uid,
                worker_version=worker_version,
                memory_limit_bytes=memory_limit_bytes,
            )
        self._json(201, {"scope": scope, "worker_id": str(worker_id)})

    def _delete_capacity_fixture_control(self, scope: str, worker_id: str) -> None:
        fixture = self._capacity_fixture(scope, require_scope=False)
        if fixture is None:
            return
        if worker_id != str(fixture.worker_id):
            self._json(404, {"error": "capacity_resource_not_found"})
            return
        with _capacity_lock:
            del _capacity_fixtures[scope]
        self._empty(204)

    def _capacity_provider_request(self, method: str) -> bool:
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) < 4 or parts[0] != "capacity" or parts[2] != "v1":
            return False
        scope = parts[1]
        if parts[3] == "capabilities" and len(parts) == 4:
            fixture = self._capacity_fixture(scope, require_scope=False)
            if fixture is not None:
                self._json(200, fixture.profile())
            return True
        if parts[3] != "workers" or len(parts) != 5:
            self._json(404, {"error": "capacity_route_not_found"})
            return True
        fixture = self._capacity_fixture(scope, require_scope=True)
        if fixture is None:
            return True
        if parts[4] != str(fixture.worker_id):
            self._json(404, {"error": "capacity_resource_not_found"})
            return True
        if method == "GET":
            self._json(200, fixture.resource())
            return True
        if method == "DELETE":
            try:
                payload = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
                )
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid_capacity_delete"})
                return True
            if (
                not isinstance(payload, dict)
                or payload.get("resource_uid") != fixture.resource_uid
            ):
                self._json(409, {"error": "capacity_resource_uid_mismatch"})
                return True
            with _capacity_lock:
                fixture.deleted = True
            self._json(200, fixture.resource())
            return True
        self._json(405, {"error": "capacity_fixture_preseeded"})
        return True

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/fixture/capacity":
            self._configure_capacity_fixture()
            return
        if self._capacity_provider_request("POST"):
            return
        if self.path != "/v1/videos":
            super().do_POST()
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.headers.get("Authorization") != "Bearer fixture-key" or not body.get("prompt"):
            self._json(400, {"error": "invalid_fixture_request"})
            return
        self._json(200, {"id": uuid4().hex})

    def do_GET(self) -> None:  # noqa: N802
        if self._capacity_provider_request("GET"):
            return
        if self.path.startswith("/v1/videos/"):
            if "/content?" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(VIDEO_BYTES)))
                self.end_headers()
                self.wfile.write(VIDEO_BYTES)
            else:
                self._json(200, {"status": "completed", "usage": {
                    "prompt_tokens": 3, "completion_tokens": 5, "cost": 0,
                }})
            return
        super().do_GET()

    def do_PUT(self) -> None:  # noqa: N802
        if self._capacity_provider_request("PUT"):
            return
        self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["fixture", "capacity"]:
            self._delete_capacity_fixture_control(parts[2], parts[3])
            return
        if self._capacity_provider_request("DELETE"):
            return
        self._json(404, {"error": "not_found"})


def main() -> None:
    directory = Path("/fixture-tls")
    directory.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.openrouter.ai")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=2))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("fixture.openrouter.ai")]), False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                   .sign(key, hashes.SHA256()))
    key_path = directory / "key.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    key_path.chmod(0o600)
    cert_path = directory / "cert.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    default_ca = ssl.get_default_verify_paths().cafile
    assert default_ca is not None
    (directory / "trust.pem").write_bytes(Path(default_ca).read_bytes() + cert_path.read_bytes())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    tls_server = ThreadingHTTPServer(("0.0.0.0", 8443), VideoHandler)
    tls_server.socket = context.wrap_socket(tls_server.socket, server_side=True)
    threading.Thread(target=tls_server.serve_forever, daemon=True).start()
    try:
        scheduler_fixture_server.main()
    finally:
        tls_server.shutdown()
        tls_server.server_close()


if __name__ == "__main__":
    main()
