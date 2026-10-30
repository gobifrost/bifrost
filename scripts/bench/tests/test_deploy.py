from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy  # noqa: E402
import envelope  # noqa: E402


class AdmittedEnvelopeEvidenceTest(unittest.TestCase):
    def _cluster(self, pods: list[dict], status: dict) -> Mock:
        cluster = Mock()
        cluster.kubectl.side_effect = [
            json.dumps({"items": pods}),
            json.dumps({"status": status}),
        ]
        return cluster

    def test_mismatch_carries_raw_admitted_pods_and_quota_status(self) -> None:
        pods = [{"metadata": {"name": "worker-1"}}]
        status = {"used": {"requests.cpu": "900m", "requests.memory": "1Gi"}}
        computed = envelope.Resources(1_000, 1024**3)
        used = envelope.Resources(900, 1024**3)
        cluster = self._cluster(pods, status)
        with patch.object(deploy.envelope, "reconcile", return_value={
            "match": False, "computed": computed, "used": used,
        }):
            with self.assertRaises(deploy.AdmittedEnvelopeError) as caught:
                deploy.reconcile_admitted(cluster)

        error = caught.exception
        self.assertIn("disagree", error.reason)
        self.assertEqual(error.evidence(), {"reason": error.reason, "pods": pods, "quota_status": status})

    def test_reconcile_error_carries_raw_admission_evidence(self) -> None:
        pods = [{"metadata": {"name": "worker-1"}}]
        status = {"used": {}}
        cluster = self._cluster(pods, status)
        with patch.object(deploy.envelope, "reconcile", side_effect=envelope.EnvelopeError("bad pod resources")):
            with self.assertRaisesRegex(deploy.AdmittedEnvelopeError, "bad pod resources") as caught:
                deploy.reconcile_admitted(cluster)

        self.assertEqual(caught.exception.pods, pods)
        self.assertEqual(caught.exception.quota_status, status)

    def test_successful_reconciliation_keeps_existing_summary(self) -> None:
        pods = [{"metadata": {"name": "worker-1"}}]
        status = {"used": {"requests.cpu": "1", "requests.memory": "1Gi"}}
        resources = envelope.Resources(1_000, 1024**3)
        cluster = self._cluster(pods, status)
        with patch.object(deploy.envelope, "reconcile", return_value={
            "match": True, "computed": resources, "used": resources,
        }):
            self.assertEqual(deploy.reconcile_admitted(cluster), {"admitted": resources.format(), "pods": 1})


if __name__ == "__main__":
    unittest.main()
