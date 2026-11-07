"""Safety boundaries for the disposable release-image rehearsal driver."""

import argparse
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


if __name__ == "__main__":
    unittest.main()
