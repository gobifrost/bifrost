from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import retention  # noqa: E402


class RetentionTest(unittest.TestCase):
    def test_requires_fresh_output_and_unique_terminal_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "used"
            path.mkdir()
            with self.assertRaisesRegex(ValueError, "fresh"):
                retention._require_fresh(path)
        execution_id = str(uuid4())
        self.assertEqual(retention._expected_ids({"terminal_records": [{"execution_id": execution_id}]}), [execution_id])
        with self.assertRaisesRegex(RuntimeError, "unique"):
            retention._expected_ids({"terminal_records": [{"execution_id": execution_id}, {"execution_id": execution_id}]})

    def test_fixture_declares_bounded_versioned_payloads_and_uuid_scoped_sql(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "incluster" / "retention_fixture.py").read_text()
        self.assertIn('VERSION = "retention-fixture-v1"', source)
        self.assertIn('"repetitive_5mb": 20', source)
        self.assertIn('"entropy_5mb": 20', source)
        self.assertIn("PAYLOAD_BYTES = 5_000_000", source)
        self.assertIn("WHERE id = ANY(CAST(:ids AS uuid[]))", source)
        self.assertNotIn("WHERE completed_at <", source)

    def test_rehearsal_hashes_raw_records_before_only_fixture_ids_are_backdated(self) -> None:
        ids = sorted([str(uuid4()), str(uuid4())])
        current = Mock()
        current.result = {}
        phases: list[tuple[str, dict]] = []
        inspect_count = 0

        def fixture(_cluster, _pod, mode: str, arguments: dict, **_kwargs):
            nonlocal inspect_count
            phases.append((mode, arguments))
            if mode == "prepare":
                return {"fixture": {"source_sha256": "a" * 64}, "workflow_ids": {"fixture": "workflow"}}
            if mode == "execute":
                return {"terminal_records": [{"execution_id": value, "raw": {"result": value}} for value in ids]}
            if mode == "inspect":
                inspect_count += 1
                return ({"execution_ids": ids, "execution_count": len(ids), "execution_log_count": 4}
                        if inspect_count < 3 else {"execution_ids": [], "execution_count": 0, "execution_log_count": 0})
            if mode == "backdate":
                self.assertIn("immutable_receipt", current.result)
                self.assertEqual(arguments["ids"], ids)
                self.assertEqual(arguments["receipt_sha256"], current.result["immutable_receipt"]["sha256"])
                return {"ids": ids}
            if mode == "maintenance":
                return {"settings": {"settings": {"days": 30}}, "job": {"status": "succeeded"}}
            raise AssertionError(mode)

        with tempfile.TemporaryDirectory() as raw, \
             patch.object(retention, "Cluster", return_value=Mock()), \
             patch.object(retention.run, "load_profile", return_value={"capacity": {}}), \
             patch.object(retention.run, "Run", return_value=current), \
             patch.object(retention.run, "_prepare", return_value={"connections": {}}), \
             patch.object(retention.run, "check_destinations"), \
             patch.object(retention.deploy, "reset"), \
             patch.object(retention.storage, "collect", return_value={"frame": "observed"}), \
             patch.object(retention, "_fixture", side_effect=fixture), \
             patch.object(retention, "_api_pod", return_value="api"):
            retention.rehearse("m", "candidate:local", Path(raw) / "evidence")
        self.assertEqual([mode for mode, _ in phases].count("maintenance"), 2)
        self.assertEqual([arguments["dry_run"] for mode, arguments in phases if mode == "maintenance"], [True, False])
        self.assertLess([mode for mode, _ in phases].index("execute"), [mode for mode, _ in phases].index("backdate"))

    def test_fixture_state_checks_exact_fixture_ids_and_cascades(self) -> None:
        ids = [str(uuid4())]
        retention._assert_state({"execution_ids": ids, "execution_count": 1, "execution_log_count": 2}, ids, deleted=False)
        retention._assert_state({"execution_ids": [], "execution_count": 0, "execution_log_count": 0}, ids, deleted=True)
        with self.assertRaisesRegex(RuntimeError, "did not delete"):
            retention._assert_state({"execution_ids": ids, "execution_count": 1, "execution_log_count": 0}, ids, deleted=True)


if __name__ == "__main__":
    unittest.main()
