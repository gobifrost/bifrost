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
                target_source="unused",
            ),
        )

    def test_project_collision_never_cleans_up_the_existing_stack(self):
        for collision in range(3):
            with self.subTest(resource=("container", "network", "volume")[collision]):
                responses = [subprocess.CompletedProcess([], 0, "", "")] * collision
                responses.append(subprocess.CompletedProcess([], 0, "existing-resource", ""))
                with patch.object(self.rehearsal, "run", side_effect=responses), patch.object(
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

    def test_direct_qualification_does_not_describe_a_bridge_release(self):
        self.assertNotIn("bridge", self.rehearsal.report)

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
