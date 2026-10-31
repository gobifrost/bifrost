from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location("bench_fake_api", Path(__file__).resolve().parents[1] / "incluster" / "fake_api.py")
assert spec and spec.loader
fake_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake_api)


class FakeApiInteractiveFixtureTest(unittest.TestCase):
    def test_site_list_is_public_deterministic_and_declared_payload_sized(self) -> None:
        payload = fake_api._integration_payload("/integration/sites", 7)
        assert payload is not None
        self.assertEqual(len(payload["items"]), 88)
        self.assertEqual(payload["items"][0], {"id": 1, "name": "Synthetic Site 001"})
        self.assertEqual(payload["payload_padding"], "x" * 7)

    def test_detail_payload_rejects_non_synthetic_site_ids(self) -> None:
        self.assertIsNone(fake_api._integration_payload("/integration/sites/0", 0))
        self.assertIsNone(fake_api._integration_payload("/integration/sites/89", 0))
        self.assertIsNone(fake_api._integration_payload("/integration/sites/nope", 0))
        detail = fake_api._integration_payload("/integration/sites/12", 0)
        assert detail is not None
        self.assertEqual(detail["devices"], [{"id": "device-012", "status": "managed"}])

    def test_controls_are_bounded_and_reject_booleanish_text(self) -> None:
        self.assertEqual(fake_api._bounded_integer({"delay_ms": "30"}, "delay_ms", 30), 30)
        for query in ({"delay_ms": "31"}, {"delay_ms": "-1"}, {"delay_ms": "true"}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                fake_api._bounded_integer(query, "delay_ms", 30)


if __name__ == "__main__":
    unittest.main()
