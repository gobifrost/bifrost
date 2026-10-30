from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy
import envelope
import run


class RuntimeProfileTest(unittest.TestCase):
    def test_import_profiles_are_opt_in_and_keep_the_m_envelope_and_full_catalog_seed(self) -> None:
        baseline = run.load_profile("m")
        profile = run.load_profile("m-import")
        smoke = run.load_profile("m-import-smoke")
        self.assertEqual(profile["quota"], baseline["quota"])
        self.assertEqual(profile["components"], baseline["components"])
        self.assertEqual(profile["worker_policy"], baseline["worker_policy"])
        self.assertEqual(profile["seed"], baseline["seed"])
        self.assertEqual(profile["table_docs"], baseline["table_docs"])
        self.assertEqual(profile["classes"], ["wf-import-control", "wf-import-graph"])
        self.assertEqual((profile["sequential"], profile["burst"], profile["burst_rounds"]), (10, 0, 0))
        self.assertEqual(profile["env"]["BIFROST_BENCH_IMPORT_TRACE"], "1")
        self.assertTrue(profile["imports_diagnostic"])
        self.assertLess(smoke["seed"]["workflow"], profile["seed"]["workflow"])
        self.assertLess(smoke["table_docs"], profile["table_docs"])

    def test_four_decimal_gb_caps_every_permitted_worker_and_preserves_cpu_and_gates(self) -> None:
        reference = run.load_profile("m-capacity")
        profile = run.load_profile("4gb-capacity")
        occupancy = envelope.max_occupancy(
            deploy.fixed_components(profile), deploy.worker_resources(profile),
            profile["worker_policy"]["max_workers"],
        )
        self.assertEqual(envelope.parse_memory(profile["quota"]["memory"]), 4_000_000_000)
        self.assertLess(occupancy.memory, 4_000_000_000)
        self.assertEqual(occupancy.memory, 3616 * 2**20)
        self.assertEqual(occupancy.cpu_m, 3900)
        self.assertEqual(profile["capacity"], reference["capacity"])
        self.assertEqual(profile["worker_cpu"], reference["worker_cpu"])
        for name, component in profile["components"].items():
            self.assertEqual(component["cpu"], reference["components"][name]["cpu"])
        deploy.check_profile(profile)


if __name__ == "__main__":
    unittest.main()
