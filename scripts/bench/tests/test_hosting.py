from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hosting  # noqa: E402


def write(path: Path, value: dict) -> str:
    path.write_text(json.dumps(value))
    return str(path)


def capacity(*, overall: str = "pass", rate: float = 10.0) -> dict:
    return {
        "profile_name": "4gb-capacity", "mix_name": "mixed", "sustainable_rate": rate,
        "confirmations": [{"overall": overall}],
        "profile": {"quota": {"cpu": "4", "memory": "8Gi", "pods": 20, "pvcs": 4, "storage": "20Gi"},
                    "components": {"api": {"cpu": "1000m", "memory": "1Gi", "replicas": 1},
                                   "postgres": {"cpu": "500m", "memory": "1Gi", "replicas": 1}},
                    "worker_cpu": "250m", "worker_policy": {"max_workers": 2, "largest_worker_mib": 512}},
        "execution_rates": {"total": {"event_rates_per_s": {"correct_completed": 9.5}}},
        "resource_summary": {"roles": {"worker": {"max_interval_cpu_cores": 0.4}}},
    }


def campaign() -> dict:
    return {"profile_name": "4gb-capacity", "mix_name": "mixed", "overall": "pass", "segments": [{"index": 0}]}


def soak() -> dict:
    return {"profile_name": "4gb-capacity", "mix_name": "mixed", "rate_per_s": 7.0, "overall": "pass",
            "storage": {"generated_bytes_per_terminal": 1024**2}, "recovery": {"status": "observed"}}


class HostingTest(unittest.TestCase):
    def scenario(self, directory: Path, *, node_cpu: str = "8", node_price: float | None = 40) -> dict:
        return {
            "evidence": {"capacity": {"path": write(directory / "capacity.json", capacity())},
                         "campaign": {"path": write(directory / "campaign.json", campaign())},
                         "soak": {"path": write(directory / "soak.json", soak())}},
            "node": {"allocatable": {"cpu": node_cpu, "memory": "16Gi"}, "other_requested": {"cpu": "0", "memory": "0"},
                     "reserve": {"cpu": "0", "memory": "0"}, "pods": 100, "pvcs": 20, "storage_gib": 1000},
            "ha_min_nodes": 2, "node_utilized_month_fraction": 1,
            "retention": {"initial_data_gib": 10, "retention_days": 30, "backup_copies": 1, "retained_versions": 1, "reserve_fraction": 0.2},
            "pricing": {"node": {"monthly_unit_price_usd": node_price}, "volume": {"per_provisioned_gib_month_usd": 0.1},
                        "model_provider": {"workflow_per_success_usd": 0.01, "agent_per_success_usd": 0.02},
                        "object_storage": {"base_monthly_usd": 5, "stored_gib": 10, "included_gib": 250,
                                           "extra_gib_month_usd": 0.02, "outbound_gib": 0, "included_outbound_gib": 1024,
                                           "extra_outbound_gib_usd": 0.01, "request_fee_monthly_usd": 0},
                        "shared_infrastructure": {"build": 5, "observability": 3, "load_balancer": 12, "network": 1, "storage": 0}},
            "demand": {name: {"successful_workflows_per_month": 1000, "successful_agent_requests_per_month": 200,
                               "peak_rate_per_s": 3, "tenant_placements": 3, "revenue_monthly_usd": 200}
                       for name in ("low", "base", "peak")},
        }

    def test_card_separates_measured_correct_rate_and_cost_bases(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            card = hosting.build_card(self.scenario(Path(raw)))
        self.assertEqual(card["certification"]["status"], "certified")
        self.assertEqual(card["workload_rates"]["configured_offered_rate_per_s"], 10.0)
        self.assertEqual(card["workload_rates"]["measured_correct_rate_per_s"], 9.5)
        self.assertEqual(card["density"]["per_node"], 4)
        base = card["cost"]["by_demand"]["base"]
        self.assertEqual(base["nodes"], 2)
        self.assertEqual(base["node_cost_usd"], 80)
        self.assertGreater(base["storage"]["provisioned_gib"], 10)
        self.assertIn("Ready node time", card["cost"]["node_billing_basis"])

    def test_unknown_price_is_null_not_zero_and_incomplete_capacity_is_not_certified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            scenario = self.scenario(directory, node_price=None)
            payload = capacity(overall="fail", rate=0)
            scenario["evidence"]["capacity"]["path"] = write(directory / "failed.json", payload)
            card = hosting.build_card(scenario)
        self.assertEqual(card["certification"]["status"], "not_certified")
        self.assertIsNone(card["cost"]["by_demand"]["base"]["node_cost_usd"])
        self.assertIn("pricing.node.monthly_unit_price_usd", card["cost"]["missing_inputs"])
        self.assertIsNone(card["workload_rates"]["configured_offered_rate_per_s"])

    def test_unconfirmed_object_request_fees_propagate_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            scenario = self.scenario(Path(raw))
            scenario["pricing"]["object_storage"]["request_fee_monthly_usd"] = None
            card = hosting.build_card(scenario)
        base = card["cost"]["by_demand"]["base"]
        self.assertIsNone(base["object_storage_cost_usd"])
        self.assertIsNone(base["combined_cost_usd"])
        self.assertIn("pricing.object_storage request or usage inputs", card["cost"]["missing_inputs"])

    def test_observed_node_capacity_can_make_profile_infeasible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            card = hosting.build_card(self.scenario(Path(raw), node_cpu="3890m"))
        self.assertEqual(card["resource_capacity"]["max_component_requests"]["cpu_m"], 2000)
        self.assertEqual(card["density"]["status"], "feasible")
        # The real 4-CPU benchmark profile has 3900m maximum components;
        # mutate this fixture to prove density uses observed allocatable CPU.
        with tempfile.TemporaryDirectory() as raw:
            scenario = self.scenario(Path(raw), node_cpu="3890m")
            artifact = json.loads(Path(scenario["evidence"]["capacity"]["path"]).read_text())
            artifact["profile"]["components"]["api"]["cpu"] = "2900m"
            Path(scenario["evidence"]["capacity"]["path"]).write_text(json.dumps(artifact))
            card = hosting.build_card(scenario)
        self.assertEqual(card["density"]["per_node"], 0)
        self.assertIsNone(card["cost"]["by_demand"]["base"]["nodes"])

    def test_mismatched_frozen_workload_and_existing_output_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            scenario = self.scenario(directory)
            bad = campaign()
            bad["mix_name"] = "sdk-heavy"
            scenario["evidence"]["campaign"]["path"] = write(directory / "bad-campaign.json", bad)
            with self.assertRaisesRegex(ValueError, "do not match"):
                hosting.build_card(scenario)
            scenario = self.scenario(directory)
            scenario_path = directory / "scenario.json"
            scenario_path.write_text(json.dumps(scenario))
            output = directory / "output"
            output.mkdir()
            with self.assertRaises(SystemExit):
                old_argv = sys.argv
                try:
                    sys.argv = ["hosting.py", "--scenario", str(scenario_path), "--out", str(output)]
                    hosting.main()
                finally:
                    sys.argv = old_argv

    def test_cli_writes_only_fresh_json_and_markdown_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            scenario_path = directory / "scenario.json"
            scenario_path.write_text(json.dumps(self.scenario(directory)))
            output = directory / "fresh-output"
            old_argv = sys.argv
            try:
                sys.argv = ["hosting.py", "--scenario", str(scenario_path), "--out", str(output)]
                hosting.main()
            finally:
                sys.argv = old_argv
            card = json.loads((output / "capacity-card.json").read_text())
            self.assertEqual(card["kind"], "hosting-capacity-card")
            self.assertIn("Hosting Capacity Card", (output / "capacity-card.md").read_text())

    def test_gib_storage_and_retention_are_not_decimal_gb_or_two_hour_extrapolation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            scenario = self.scenario(Path(raw))
            scenario["retention"]["initial_data_gib"] = 1
            card = hosting.build_card(scenario)
        storage = card["cost"]["by_demand"]["base"]["storage"]
        # 1 MiB terminal generation x 1,200 monthly successes is 1.171875 GiB.
        self.assertAlmostEqual(storage["provisioned_gib"], (1 + 1200 / 1024) * 3 * 1.2)
        self.assertIn("explicit per-terminal", storage["basis"])


if __name__ == "__main__":
    unittest.main()
