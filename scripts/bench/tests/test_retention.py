from __future__ import annotations

import sys
import tempfile
import types
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import retention  # noqa: E402


class RetentionTest(unittest.TestCase):
    @staticmethod
    def _fixture_module():
        path = Path(__file__).resolve().parents[1] / "incluster" / "retention_fixture.py"
        stub = types.ModuleType("httpx")
        previous = sys.modules.get("httpx")
        sys.modules["httpx"] = stub
        try:
            spec = spec_from_file_location("retention_fixture_test", path)
            assert spec and spec.loader
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            if previous is None:
                del sys.modules["httpx"]
            else:
                sys.modules["httpx"] = previous

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
        self.assertIn('"repetitive_5mb": {"workflow"', source)
        self.assertIn('"entropy_5mb": {"workflow"', source)
        self.assertIn("PAYLOAD_BYTES = 5_000_000", source)
        self.assertIn("WHERE id = ANY(CAST(:ids AS uuid[]))", source)
        self.assertNotIn("WHERE completed_at <", source)

    def test_rehearsal_hashes_raw_records_before_only_fixture_ids_are_backdated(self) -> None:
        ids = sorted([str(uuid4()) for _ in range(4)])
        current = Mock()
        current.result = {}
        phases: list[tuple[str, dict]] = []
        inspect_count = 0

        def fixture(_cluster, _pod, mode: str, arguments: dict, **_kwargs):
            nonlocal inspect_count
            phases.append((mode, arguments))
            if mode == "prepare":
                return {"fixture": {"source_sha256": "a" * 64}, "workflow_ids": {"fixture": "workflow"}}
            if mode == "settings":
                return {"settings": {"days": 30}}
            if mode == "execute":
                case = arguments["case"]
                return {"terminal_records": [{"execution_id": ids[len([item for item in phases if item[0] == "execute"]) - 1],
                                               "case": case, "raw": {"result": case}}]}
            if mode == "inspect":
                inspect_count += 1
                return ({"execution_ids": ids, "execution_count": len(ids), "execution_log_count": 4}
                        if inspect_count < 3 else {"execution_ids": [], "execution_count": 0, "execution_log_count": 0})
            if mode == "backdate":
                self.assertIn("immutable_receipt", current.result)
                self.assertEqual(arguments["ids"], ids)
                self.assertEqual(arguments["receipt_sha256"], current.result["immutable_receipt"]["sha256"])
                self.assertEqual(arguments["age_days"], 31)
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
            out = Path(raw) / "evidence"
            current.save.side_effect = lambda: out.mkdir(exist_ok=True)
            retention.rehearse("m", "candidate:local", out)
            receipt = current.result["immutable_receipt"]
            self.assertEqual(Path(receipt["path"]).read_bytes().__len__(), receipt["bytes"])
        self.assertEqual([mode for mode, _ in phases].count("maintenance"), 2)
        self.assertEqual([arguments["dry_run"] for mode, arguments in phases if mode == "maintenance"], [True, False])
        self.assertLess([mode for mode, _ in phases].index("execute"), [mode for mode, _ in phases].index("backdate"))

    def test_keep_forever_is_not_exercised(self) -> None:
        current = Mock()
        current.result = {}
        with tempfile.TemporaryDirectory() as raw, \
             patch.object(retention, "Cluster", return_value=Mock()), \
             patch.object(retention.run, "load_profile", return_value={"capacity": {}}), \
             patch.object(retention.run, "Run", return_value=current), \
             patch.object(retention.run, "_prepare", return_value={"connections": {}}), \
             patch.object(retention.run, "check_destinations"), \
             patch.object(retention.deploy, "reset"), \
             patch.object(retention.storage, "collect", return_value={"frame": "observed"}), \
             patch.object(retention, "_fixture", side_effect=[
                 {"fixture": {"source_sha256": "a" * 64}, "workflow_ids": {}}, {"settings": {"days": None}},
             ]):
            out = Path(raw) / "evidence"
            current.save.side_effect = lambda: out.mkdir(exist_ok=True)
            retention.rehearse("m", "candidate:local", out)
        self.assertEqual(current.result["stage"], "not_exercised")
        self.assertNotIn("retention_actual", current.result)

    def test_fixture_state_checks_exact_fixture_ids_and_cascades(self) -> None:
        ids = [str(uuid4())]
        retention._assert_state({"execution_ids": ids, "execution_count": 1, "execution_log_count": 2}, ids, deleted=False)
        retention._assert_state({"execution_ids": [], "execution_count": 0, "execution_log_count": 0}, ids, deleted=True)
        with self.assertRaisesRegex(RuntimeError, "did not delete"):
            retention._assert_state({"execution_ids": ids, "execution_count": 1, "execution_log_count": 0}, ids, deleted=True)

    def test_failure_is_saved_before_namespace_cleanup_can_block(self) -> None:
        current = Mock()
        current.result = {}
        saved = []
        current.save.side_effect = lambda: saved.append(dict(current.result))
        reset_count = 0

        def reset(_cluster):
            nonlocal reset_count
            reset_count += 1
            if reset_count == 2:
                self.assertEqual(saved[-1]["stage"], "exception")
                self.assertEqual(saved[-1]["error"], "RuntimeError: fixture setup failed")

        with tempfile.TemporaryDirectory() as raw, \
             patch.object(retention, "Cluster", return_value=Mock()), \
             patch.object(retention.run, "load_profile", return_value={}), \
             patch.object(retention.run, "Run", return_value=current), \
             patch.object(retention.run, "_prepare", side_effect=RuntimeError("fixture setup failed")), \
             patch.object(retention.deploy, "reset", side_effect=reset):
            with self.assertRaisesRegex(RuntimeError, "fixture setup failed"):
                retention.rehearse("m", "candidate:local", Path(raw) / "evidence")
        self.assertTrue(current.result["cleanup"]["verified"])

    def test_execute_submits_signature_safe_inputs_and_validates_terminal_payload(self) -> None:
        fixture = self._fixture_module()
        submitted: list[dict] = []

        class Response:
            status_code = 200
            text = ""

            def __init__(self, body: dict):
                self.body = body

            def json(self) -> dict:
                return self.body

        class Client:
            async def post(self, _path: str, json: dict) -> Response:
                submitted.append(json)
                return Response({"execution_id": str(uuid4())})

            async def get(self, _path: str) -> Response:
                payload = "x" * fixture.PAYLOAD_BYTES
                return Response({"status": "Success", "result": {"payload": payload}})

            async def aclose(self) -> None:
                return None

        async def client() -> Client:
            return Client()

        cases = {
            "small_success": {"workflow": "small", "count": 1, "expected_status": "Success", "large": False},
            "repetitive_5mb": {"workflow": "large", "count": 1, "expected_status": "Success", "large": True},
        }
        with patch.object(fixture, "CASES", cases), patch.object(fixture, "_client", client):
            import asyncio

            small = asyncio.run(fixture.execute({"small": "small-id"}, "small_success"))
            large = asyncio.run(fixture.execute({"large": "large-id"}, "repetitive_5mb"))
        self.assertEqual(submitted[0]["input_data"], {"seed": 0})
        self.assertEqual(submitted[1]["input_data"], {"seed": 0, "log_lines": 8})
        self.assertEqual(small["terminal_records"][0]["validation"]["expected_status"], "Success")
        self.assertEqual(large["terminal_records"][0]["validation"]["payload_utf8_bytes"], fixture.PAYLOAD_BYTES)
        with self.assertRaisesRegex(RuntimeError, "ended"):
            fixture._validate_terminal("small_success", {"execution_id": "fixture"}, {"status": "Failed"})


if __name__ == "__main__":
    unittest.main()
