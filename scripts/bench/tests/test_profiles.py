from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy
import envelope
import run


class RuntimeProfileTest(unittest.TestCase):
    def test_startup_policy_matches_each_declared_envelope_before_first_warm_job(self) -> None:
        for name in ("m", "4gb-capacity"):
            with self.subTest(profile=name):
                profile = run.load_profile(name)
                patches = deploy.tenant_patches(profile, "127.0.0.1")
                config_patch = next(patch for patch in patches if patch["target"] == {
                    "kind": "ConfigMap", "name": "bifrost-config",
                })
                config = {operation["path"].removeprefix("/data/"): operation["value"]
                          for operation in json.loads(config_patch["patch"])}
                # These are the canonical import_kubernetes_policy_from_env
                # inputs, consumed before the controller launches warm Jobs.
                self.assertEqual(int(config["BIFROST_POOL_MIN_WORKERS"]), profile["worker_policy"]["warm_workers"])
                self.assertEqual(int(config["BIFROST_POOL_MAX_WORKERS"]), profile["worker_policy"]["max_workers"])
                self.assertEqual(int(config["BIFROST_POOL_WORKER_MAX_MEMORY_MIB"]), profile["worker_policy"]["largest_worker_mib"])
                self.assertEqual(config["BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB"], config["BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB"])
                self.assertEqual(int(config["BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB"]), profile["worker_policy"]["worker_memory_mib"])

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

    def test_import_overlap_holds_one_warm_worker_with_the_same_import_workload(self) -> None:
        baseline = run.load_profile("m-import")
        profile = run.load_profile("m-import-overlap")
        for key in ("quota", "components", "worker_cpu", "seed", "table_docs", "classes", "env", "imports_diagnostic"):
            self.assertEqual(profile[key], baseline[key], key)
        self.assertEqual(profile["worker_policy"], {**baseline["worker_policy"], "max_workers": 1})
        self.assertEqual((profile["sequential"], profile["burst"], profile["burst_rounds"]), (10, 3, 5))
        deploy.check_profile(profile)


if __name__ == "__main__":
    unittest.main()
