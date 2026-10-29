"""Local provider fixture for the platform-job integration suite.

The fixture retains the scheduler suite's HTTP OAuth, embeddings, and MCP
responses while adding a local TLS video provider. The container-only DNS alias
preserves the production OpenRouter hostname checks; a generated short-lived
certificate preserves TLS verification. No external provider request or real
credential is involved.
"""

from __future__ import annotations

import json
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts import scheduler_fixture_server

VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


class VideoHandler(scheduler_fixture_server.FixtureHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/videos":
            super().do_POST()
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.headers.get("Authorization") != "Bearer fixture-key" or not body.get("prompt"):
            self._json(400, {"error": "invalid_fixture_request"})
            return
        self._json(200, {"id": uuid4().hex})

    def do_GET(self) -> None:  # noqa: N802
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
