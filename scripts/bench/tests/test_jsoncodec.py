from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsoncodec  # noqa: E402


class JsonCodecTest(unittest.TestCase):
    def test_round_trips_nested_non_finite_values_without_mutating_source(self) -> None:
        source = {
            "schema_version": 2,
            "gates": {"queue": float("inf"), "nested": [1, {"low": float("-inf")}]},
            "raw_failed_records": [{"error": float("nan")}],
        }
        original = copy.deepcopy(source)

        encoded = jsoncodec.encode(source)

        self.assertEqual(source["schema_version"], original["schema_version"])
        self.assertTrue(math.isinf(source["gates"]["queue"]))
        self.assertTrue(math.isinf(source["gates"]["nested"][1]["low"]))
        self.assertTrue(math.isnan(source["raw_failed_records"][0]["error"]))
        self.assertEqual(encoded["schema_version"], 3)
        self.assertEqual(
            encoded["non_finite_numbers"],
            [
                {"path": ["gates", "nested", 1, "low"], "kind": "negative_infinity"},
                {"path": ["gates", "queue"], "kind": "positive_infinity"},
                {"path": ["raw_failed_records", 0, "error"], "kind": "nan"},
            ],
        )
        self.assertIsNone(encoded["gates"]["queue"])

        decoded = jsoncodec.decode(encoded)

        self.assertTrue(
            math.isinf(decoded["gates"]["queue"]) and decoded["gates"]["queue"] > 0
        )
        self.assertTrue(
            math.isinf(decoded["gates"]["nested"][1]["low"])
            and decoded["gates"]["nested"][1]["low"] < 0
        )
        self.assertTrue(math.isnan(decoded["raw_failed_records"][0]["error"]))
        self.assertNotIn("non_finite_numbers", decoded)
        self.assertEqual(jsoncodec.encode(decoded), encoded)

    def test_encodes_realistic_nested_tuples_as_json_arrays(self) -> None:
        source = {
            "machine": {"load_average": (0.1, float("inf"), 0.3)},
            "database": {"top_tables": (("runs", 12), ("events", 7))},
            "step": {"window": (10, 20)},
            "reliability_evidence": (("class_a", 400),),
        }

        encoded = jsoncodec.encode(source)

        self.assertEqual(encoded["machine"]["load_average"], [0.1, None, 0.3])
        self.assertEqual(encoded["database"]["top_tables"], [["runs", 12], ["events", 7]])
        self.assertEqual(encoded["step"]["window"], [10, 20])
        self.assertEqual(
            encoded["non_finite_numbers"],
            [{"path": ["machine", "load_average", 1], "kind": "positive_infinity"}],
        )
        self.assertIsInstance(source["machine"]["load_average"], tuple)

        decoded = jsoncodec.decode(encoded)

        self.assertEqual(decoded["step"]["window"], [10, 20])
        self.assertTrue(math.isinf(decoded["machine"]["load_average"][1]))

    def test_encoded_result_is_strict_json_and_parser_rejects_nonstandard_constants(self) -> None:
        encoded = jsoncodec.encode({"value": float("nan")})
        serialized = json.dumps(encoded, allow_nan=False)
        self.assertEqual(json.loads(serialized)["value"], None)
        with self.assertRaises(ValueError):
            json.loads('{"value": NaN}', parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))

    def test_finite_values_none_and_booleans_are_preserved(self) -> None:
        source = {"number": 2.5, "none": None, "truth": True, "falsehood": False, "items": [0, False, None]}
        encoded = jsoncodec.encode(source)
        self.assertEqual(encoded["non_finite_numbers"], [])
        self.assertEqual(encoded["number"], 2.5)
        self.assertEqual(encoded["items"], [0, False, None])
        self.assertEqual(
            jsoncodec.decode(encoded),
            {**source, "schema_version": jsoncodec.SCHEMA_VERSION},
        )

    def test_metadata_order_is_deterministic(self) -> None:
        first = jsoncodec.encode(
            {"z": float("nan"), "a": [float("inf"), {"b": float("-inf")}]}
        )
        second = jsoncodec.encode(
            {"a": [float("inf"), {"b": float("-inf")}], "z": float("nan")}
        )
        self.assertEqual(first["non_finite_numbers"], second["non_finite_numbers"])

    def test_rejects_reserved_collision_and_unsupported_values_or_keys(self) -> None:
        with self.assertRaises(ValueError):
            jsoncodec.encode({"non_finite_numbers": []})
        with self.assertRaises(ValueError):
            jsoncodec.encode({1: "not a JSON key"})
        with self.assertRaises(ValueError):
            jsoncodec.encode({"bad": object()})

    def test_rejects_malformed_v3_metadata_before_mutating_source(self) -> None:
        base = jsoncodec.encode({"nested": [float("inf")]})
        invalid_variants = [
            {**base, "non_finite_numbers": {"path": ["nested", 0], "kind": "nan"}},
            {
                **base,
                "non_finite_numbers": [
                    {"path": ["nested", 0], "kind": "nan", "extra": True}
                ],
            },
            {**base, "non_finite_numbers": [*base["non_finite_numbers"], *base["non_finite_numbers"]]},
            {**base, "non_finite_numbers": [{"path": ["missing"], "kind": "nan"}]},
            {**base, "non_finite_numbers": [{"path": ["nested", 0], "kind": "nan"}], "nested": [0]},
            {**base, "non_finite_numbers": [{"path": ["nested", True], "kind": "nan"}]},
            {**base, "non_finite_numbers": [{"path": ["nested", 1], "kind": "nan"}]},
            {**base, "non_finite_numbers": [{"path": ["nested", 0], "kind": "not-a-kind"}]},
        ]
        for invalid in invalid_variants:
            with self.subTest(invalid=invalid["non_finite_numbers"]):
                before = copy.deepcopy(invalid)
                with self.assertRaises(ValueError):
                    jsoncodec.decode(invalid)
                self.assertEqual(invalid, before)

    def test_legacy_v2_or_artifact_without_metadata_is_copied_without_rewrite(self) -> None:
        for legacy in (
            {"schema_version": 1, "value": float("inf")},
            {"schema_version": 2, "value": None},
            {"value": [1, float("inf")]},
        ):
            with self.subTest(legacy=legacy):
                decoded = jsoncodec.decode(legacy)
                self.assertIsNot(decoded, legacy)
                self.assertEqual(decoded, legacy)

    def test_rejects_unknown_or_ambiguous_schema_versions_and_legacy_sidecars(self) -> None:
        invalid_artifacts = [
            {"schema_version": 4},
            {"schema_version": True},
            {"schema_version": 2.0},
            {"schema_version": 3.0},
            {"schema_version": 3},
            {"schema_version": 2, "non_finite_numbers": []},
            {"non_finite_numbers": []},
        ]
        for artifact in invalid_artifacts:
            with self.subTest(artifact=artifact):
                with self.assertRaises(ValueError):
                    jsoncodec.decode(artifact)


if __name__ == "__main__":
    unittest.main()
