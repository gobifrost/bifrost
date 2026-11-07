"""Synthetic GitHub App wire contract, served only in the isolated test network."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
import jwt

from scripts.scheduler_fixture_server import FixtureHandler

APP_ID = 8000123
INSTALLATIONS = {70001: 80001, 70002: 80002}
KEY_PATH = Path("/fixture-tls/github-app-key.pem")


class GitHubAppFixture(FixtureHandler):
    def _app_auth(self) -> bool:
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
        try:
            jwt.decode(token, key.public_key(), algorithms=["RS256"], issuer=str(APP_ID),
                       options={"require": ["iss", "iat", "exp"]})
            return True
        except jwt.InvalidTokenError:
            self._json(401, {"message": "Synthetic App JWT required"})
            return False

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"status": "ok"})
            return
        if path == "/installation/repositories":
            token = self.headers.get("Authorization", "")
            identity = next((identity for identity in INSTALLATIONS if token == f"Bearer fixture-installation-{identity}"), None)
            if identity is None:
                self._json(401, {"message": "Synthetic installation token required"})
            else:
                self._json(200, {"total_count": 1, "repositories": [{"id": INSTALLATIONS[identity], "full_name": f"fixture/repository-{identity}", "private": True}]})
            return
        if not self._app_auth():
            return
        if path == "/app":
            self._json(200, {"id": APP_ID, "slug": "bifrost-fixture-app"})
        elif path == "/app/hook/deliveries":
            self._json(200, [])
        elif path.startswith("/app/installations/") and path.rsplit("/", 1)[-1].isdigit():
            identity = int(path.rsplit("/", 1)[-1])
            if identity in INSTALLATIONS:
                self._json(200, {"id": identity, "app_id": APP_ID, "account": {"id": identity + 100000, "login": f"fixture-{identity}"},
                                 "permissions": {"metadata": "read", "issues": "read", "pull_requests": "read", "checks": "read", "contents": "read"}, "suspended_at": None})
            else:
                self._json(404, {"message": "Installation not found"})
        else:
            self._json(404, {"message": "Unknown synthetic GitHub endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._app_auth():
            return
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["app", "installations"] and parts[2].isdigit() and parts[3] == "access_tokens":
            identity = int(parts[2])
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if identity not in INSTALLATIONS or body.get("permissions") != {"metadata": "read"}:
                self._json(403, {"message": "Unsupported synthetic installation scope"})
                return
            self._json(201, {"token": f"fixture-installation-{identity}", "permissions": body["permissions"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "repositories": [{"id": INSTALLATIONS[identity]}]})
            return
        self._json(404, {"message": "Unknown synthetic GitHub endpoint"})
