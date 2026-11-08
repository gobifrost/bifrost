"""Safety boundaries for the disposable release-image rehearsal driver."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.release.stable_2_upgrade_rehearsal import Rehearsal


class RehearsalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        root = Path(self.scratch.name)
        self.rehearsal = Rehearsal(
            argparse.Namespace(
                run_id="test",
                evidence_dir=root,
                target_api_image="unused",
                target_client_image="unused-client",
                browser_runner_image="sha256:" + "a" * 64,
                target_source="unused",
            ),
        )

    def test_project_collision_never_cleans_up_the_existing_stack(self):
        for collision in range(3):
            with self.subTest(resource=("container", "network", "volume")[collision]):
                responses = [subprocess.CompletedProcess([], 0, "", "")] * collision
                responses.append(subprocess.CompletedProcess([], 0, "existing-resource", ""))
                with patch.object(Path, "is_file", return_value=True), patch.object(self.rehearsal, "run", side_effect=responses), patch.object(
                    self.rehearsal, "dc"
                ) as compose:
                    with self.assertRaisesRegex(RuntimeError, "existing Compose project"):
                        self.rehearsal.execute()
                    self.rehearsal.cleanup()
                    compose.assert_not_called()

    def test_credentials_reach_compose_without_a_plaintext_file(self):
        with patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")
        ) as process:
            self.rehearsal.dc("config", "--quiet")
        environment = process.call_args.kwargs["env"]
        self.assertTrue(
            environment.get("REHEARSAL_PASSWORD") == self.rehearsal.password,
            "Compose must receive its generated password through its environment",
        )
        self.assertTrue(
            environment.get("REHEARSAL_SECRET") == self.rehearsal.secret,
            "Compose must receive its generated key through its environment",
        )
        for path in Path(self.scratch.name).rglob("*"):
            if path.is_file():
                contents = path.read_text()
                self.assertFalse(self.rehearsal.password in contents, "Password must not be persisted")
                self.assertFalse(self.rehearsal.secret in contents, "Key must not be persisted")

    def test_recovery_project_collision_never_cleans_up_an_existing_stack(self):
        self.rehearsal.owns_project = True
        with patch.object(
            self.rehearsal, "run",
            return_value=subprocess.CompletedProcess([], 0, "existing-resource", ""),
        ), patch.object(self.rehearsal, "dc") as compose:
            with self.assertRaisesRegex(RuntimeError, "existing Compose project"):
                self.rehearsal.rehearse_pending_recovery()
            self.rehearsal.cleanup()
        self.assertFalse(self.rehearsal.owns_project)
        self.assertEqual(compose.call_count, 1)
        self.assertEqual(compose.call_args.args, ("down", "--volumes", "--remove-orphans"))
        self.assertEqual(self.rehearsal.report["project_cleanup"], {"bifrost-upgrade-test": True})

    def test_failed_cleanup_leaves_a_durable_failure_receipt(self):
        self.rehearsal.owns_project = True
        self.rehearsal.report["passed"] = True
        with patch.object(
            self.rehearsal, "dc", side_effect=RuntimeError("Docker failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "Docker failed"):
                self.rehearsal.cleanup()
        receipt = json.loads((self.rehearsal.evidence / "receipt.json").read_text())
        self.assertFalse(receipt["cleanup_completed"])
        self.assertFalse(receipt["passed"])

    def test_failed_commands_redact_synthetic_credentials_from_evidence(self):
        raw = f"database://{self.rehearsal.password} key={self.rehearsal.secret}"
        with patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, raw, ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "sanitized diagnostics"):
                self.rehearsal.run(["unused"])
        saved = (self.rehearsal.evidence / "failure.log").read_text()
        self.assertNotIn(self.rehearsal.password, saved)
        self.assertNotIn(self.rehearsal.secret, saved)
        self.assertIn("[redacted]", saved)

    def test_population_evidence_excludes_the_legacy_human_token(self):
        payload = {
            "state": {"organization_ids": ["org-a", "org-b"]},
            "evidence": {"organizations": 2, "old_app_deployed": True},
            "legacy_access_token": "private-in-memory-test-value",
        }
        with patch.object(Path, "read_text", return_value="# helper"), patch.object(
            self.rehearsal, "dc",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
        ) as compose:
            self.rehearsal.population("seed")
        self.assertEqual(self.rehearsal.population_payload, payload)
        self.assertEqual(json.loads(compose.call_args.kwargs["stdin"]), {})
        receipt = (self.rehearsal.evidence / "receipt.json").read_text()
        self.assertNotIn(payload["legacy_access_token"], receipt)
        self.assertNotIn("legacy_access_token", receipt)
        self.assertEqual(json.loads(receipt)["population"]["seed"], payload["evidence"])

    def test_private_helper_partial_stdout_is_never_saved_on_failure(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, '{"access_token":"synthetic-token"}', "safe-phase-failure")):
            with self.assertRaisesRegex(RuntimeError, "sanitized diagnostics"):
                self.rehearsal.run(["unused"], private_stdout=True)
        saved = (self.rehearsal.evidence / "failure.log").read_text()
        self.assertNotIn("synthetic-token", saved)
        self.assertIn("safe-phase-failure", saved)

    def test_service_diagnostics_redact_query_credentials_and_signed_tokens(self):
        self.rehearsal.owns_project = True
        raw = f'GET /ws/connect?token=opaque-private-token Bearer eyJhbGciOiJIUzI1NiJ9.cHJpdmF0ZQ.c2lnbmF0dXJl database={self.rehearsal.password}'
        with patch.object(self.rehearsal, "dc", return_value=subprocess.CompletedProcess([], 0, raw, "")):
            self.rehearsal.capture_failure_diagnostics()
        saved = (self.rehearsal.evidence / "service-diagnostics.log").read_text()
        self.assertNotIn("opaque-private-token", saved)
        self.assertNotIn("eyJhbGci", saved)
        self.assertNotIn(self.rehearsal.password, saved)
        self.assertIn("/ws/connect?token=[redacted]", saved)

    def test_direct_qualification_does_not_describe_a_bridge_release(self):
        self.assertNotIn("bridge", self.rehearsal.report)

    def test_missing_helper_refuses_before_creating_resources(self):
        with patch.object(Path, "is_file", return_value=False), patch.object(self.rehearsal, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Missing qualification helper"):
                self.rehearsal.execute()
        run.assert_not_called()
        self.assertFalse(self.rehearsal.owns_project)

    def test_browser_receipt_requires_all_observations_and_excludes_private_fields(self):
        proof = {key: True for key in ("passed", "initial_sdk2_query_token_verified", "expiry_recovered", "epoch_recovered", "local_state_preserved", "revoked_family_refused")}
        proof.update(browser_refreshes=2, table_subscription_acks=3, page_errors=0, access_token="private-test-value")
        self.rehearsal.record_browser_proof(proof)
        self.assertNotIn("access_token", self.rehearsal.report["original_app_browser"])
        for key in ("expiry_recovered", "epoch_recovered", "revoked_family_refused"):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "required assertion"):
                self.rehearsal.record_browser_proof({**proof, key: False})
        with self.assertRaisesRegex(RuntimeError, "insufficient observations"):
            self.rehearsal.record_browser_proof({**proof, "browser_refreshes": 1})

    def test_compose_defines_a_distinct_pinned_old_worker(self):
        compose = (Path(__file__).parent / "release" / "stable_2_upgrade_compose.yml").read_text()
        self.assertIn("old-worker:", compose)
        self.assertIn("REHEARSAL_OLD_WORKER_IMAGE", compose)

    def test_old_worker_probe_waits_for_target_release_before_reading_secret(self):
        self.rehearsal.population_payload = {
            "state": {
                "configs": {"a_key": "org-config"},
                "organizations": {"a": "11111111-1111-4111-8111-111111111111"},
            }
        }
        code = self.rehearsal.old_worker_probe_code()
        self.assertLess(code.index("STABLE_2_OLD_WORKER_BARRIER_REACHED"), code.index('await config.get("org-config", scope="11111111-1111-4111-8111-111111111111")'))
        self.assertIn('Path("/tmp/stable2-upgrade-release").exists()', code)
        self.assertIn("STABLE_2_OLD_WORKER_TARGET_CONFIG_SUCCESS", code)

    def test_old_worker_proof_is_derived_privately_without_serialized_credentials(self):
        from scripts.release.stable_2_upgrade_population import secret_value

        self.rehearsal.population_payload = {
            "state": {
                "configs": {"a_key": "org-config"},
                "organizations": {"a": "11111111-1111-4111-8111-111111111111"},
            }
        }
        with patch.dict("os.environ", {"BIFROST_DEFAULT_USER_PASSWORD": self.rehearsal.password}):
            fixture_value = secret_value("config-a")
        code = self.rehearsal.old_worker_probe_code()
        self.assertTrue(hashlib.sha256(fixture_value.encode()).hexdigest() in code, "Probe must compare the seeded fixture")
        self.assertFalse(fixture_value in code, "Raw fixture values must not reach probe source")
        self.assertFalse(self.rehearsal.password in code, "Controller credentials must not reach probe source")

    def test_running_source_barrier_does_not_depend_on_completed_execution_logs(self):
        with patch.object(self.rehearsal, "sql", return_value="Running") as sql, patch.object(
            self.rehearsal, "dc", return_value=subprocess.CompletedProcess([], 0, "", "")
        ) as compose:
            self.assertTrue(self.rehearsal.old_worker_at_barrier("run-id"))
        self.assertNotIn("execution_logs", sql.call_args.args[0])
        self.assertEqual(compose.call_args.args, ("exec", "-T", "old-worker", "test", "-f", "/tmp/stable2-upgrade-reached"))
        self.assertIn('Path("/tmp/stable2-upgrade-reached").touch()', self._probe_code())

    def _probe_code(self):
        self.rehearsal.population_payload = {"state": {
            "configs": {"a_key": "org-config"},
            "organizations": {"a": "11111111-1111-4111-8111-111111111111"},
        }}
        return self.rehearsal.old_worker_probe_code()

    def test_barrier_refuses_a_source_execution_that_already_ended(self):
        for status in ("Failed", "Cancelled", "Timeout", "Success"):
            with self.subTest(status=status), patch.object(self.rehearsal, "sql", return_value=status), patch.object(self.rehearsal, "dc") as compose:
                with self.assertRaisesRegex(RuntimeError, "ended before release"):
                    self.rehearsal.old_worker_at_barrier("run-id")
                compose.assert_not_called()

    def test_pending_recovery_preflights_its_second_compose_project(self):
        with patch.object(self.rehearsal, "cleanup"), patch.object(
            self.rehearsal,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "existing-resource", ""),
        ), patch.object(self.rehearsal, "dc") as compose:
            with self.assertRaisesRegex(RuntimeError, "existing Compose project"):
                self.rehearsal.rehearse_pending_recovery()
        compose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
