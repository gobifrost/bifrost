"""Pure unit coverage for the pinned-image upgrade population helper."""

from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
from types import ModuleType
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch
import zipfile


MODULE_PATH = Path(__file__).parent / "release" / "stable_2_upgrade_population.py"
SPEC = importlib.util.spec_from_file_location("stable_2_upgrade_population", MODULE_PATH)
assert SPEC and SPEC.loader
population = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = population
SPEC.loader.exec_module(population)


class UpgradePopulationTests(unittest.TestCase):
    def test_fixture_credentials_are_repeatable_and_separated_by_purpose(self) -> None:
        with patch.dict(population.os.environ, {"BIFROST_DEFAULT_USER_PASSWORD": "synthetic-root-for-unit-test"}):
            password = population.fixture_password("human-a")
            self.assertEqual(password, population.fixture_password("human-a"))
            self.assertNotEqual(password, population.fixture_password("human-b"))
            self.assertNotEqual(password, population.secret_value("human-a"))

    def test_source_archive_preserves_sdk_bound_mount_source(self) -> None:
        archive = population.source_zip()
        population.assert_retained_source(archive, "test", "/source")
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            self.assertEqual(json.loads(opened.read("package.json"))["devDependencies"]["vite"], "6.2.2")
            self.assertIn(b"from 'bifrost'", opened.read("src/main.js"))

    def test_retained_source_failure_never_includes_archive_contents(self) -> None:
        with self.assertRaises(population.ScenarioError) as error:
            population.assert_retained_source(b"not a zip: fixture-secret", "test", "/source")
        self.assertNotIn("fixture-secret", str(error.exception))
        self.assertIn("retained source archive was incomplete", str(error.exception))

    def test_source_change_is_detected_even_when_sdk_import_is_preserved(self) -> None:
        changed = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(population.source_zip())) as original, zipfile.ZipFile(changed, "w") as replacement:
            for name in original.namelist():
                content = original.read(name)
                if name == "src/main.js":
                    content = content.replace(b"stable-2 upgrade fixture", b"changed application")
                replacement.writestr(name, content)
        with self.assertRaisesRegex(population.ScenarioError, "source file changed"):
            population.assert_retained_source(changed.getvalue(), "verify", "/source")

    def test_changed_legacy_bundle_is_rejected_even_with_the_same_deployment(self) -> None:
        class Http:
            artifact = b"original deployed bundle"

            def json(self, method, path, **kwargs):
                if path.endswith("bundle-manifest"):
                    return {"entry": "main.js", "runtime_contract": "mount-v1"}
                return {"sdk_contract_version": 2}

            def request(self, method, path, **kwargs):
                return SimpleNamespace(body=population.source_zip() if path.endswith("/source") else self.artifact)

        http = Http()
        application = {"id": "app-id", "slug": "app", "deployment_id": "original"}
        application["bundle"] = population.active_bundle_fingerprint(http, "app-id", "seed", "token")
        with patch.object(population, "active_deployment_id", return_value="original"):
            population.assert_active_legacy_bundle(http, {"application": application}, "verify", "token")
            http.artifact = b"changed deployed bundle"
            with self.assertRaisesRegex(population.ScenarioError, "legacy application artifact changed"):
                population.assert_active_legacy_bundle(http, {"application": application}, "verify", "token")

    def test_activation_reads_do_not_reuse_connections_from_closed_loops(self) -> None:
        owner = None
        disposals = 0

        class Engine:
            async def dispose(self):
                nonlocal owner, disposals
                owner = None
                disposals += 1

        class Session:
            async def scalar(self, *_args, **_kwargs):
                nonlocal owner
                loop = asyncio.get_running_loop()
                if owner is not None and owner is not loop:
                    raise RuntimeError("connection belongs to a different loop")
                owner = loop
                return "active-deployment"

        @asynccontextmanager
        async def context():
            yield Session()

        database = ModuleType("src.core.database")
        database.get_db_context = context
        database.get_engine = lambda: Engine()
        sqlalchemy = ModuleType("sqlalchemy")
        sqlalchemy.text = lambda statement: statement
        modules = {"src": ModuleType("src"), "src.core": ModuleType("src.core"), "src.core.database": database, "sqlalchemy": sqlalchemy}
        app_id = "11111111-1111-4111-8111-111111111111"
        with patch.dict(sys.modules, modules):
            self.assertEqual(population.active_deployment_id(app_id, "verify"), "active-deployment")
            self.assertEqual(population.active_deployment_id(app_id, "verify"), "active-deployment")
        self.assertEqual(disposals, 2)

    def test_terminal_poll_propagates_authorization_token(self) -> None:
        calls: list[tuple[str, str | None]] = []

        class FakeHttp:
            phase = "test"

            def json(self, method: str, path: str, *, token: str):
                calls.append((path, token))
                return {"status": "succeeded"}

        result = population.wait_terminal(FakeHttp(), "/api/platform-jobs/job", token="private-token", timeout=1, success=("succeeded",))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(calls, [("/api/platform-jobs/job", "private-token")])

    def test_http_error_reports_status_without_response_body(self) -> None:
        error = urllib.error.HTTPError("http://localhost:8000/private", 403, "forbidden", {}, io.BytesIO(b"response-secret"))
        with patch.object(population.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(population.ScenarioError) as raised:
                population.Http("verify").request("GET", "/private", token="token-secret")
        message = str(raised.exception)
        self.assertIn("status=403", message)
        self.assertNotIn("response-secret", message)
        self.assertNotIn("token-secret", message)

    def test_jwt_expiry_reading_accepts_future_expiry_without_returning_claims(self) -> None:
        payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 60}).encode()).decode().rstrip("=")
        self.assertGreater(population.legacy_token_expiry(f"header.{payload}.signature"), time.time())

    def test_jwt_expiry_reading_rejects_invalid_token_safely(self) -> None:
        with self.assertRaises(population.ScenarioError) as raised:
            population.legacy_token_expiry("secret-token")
        self.assertNotIn("secret-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
