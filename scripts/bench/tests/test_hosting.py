from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hosting  # noqa: E402


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_reference(path: Path, value: dict) -> dict[str, str]:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def profile() -> dict:
    return {
        "quota": {"cpu": "4", "memory": "8Gi", "pods": 20, "pvcs": 4, "storage": "20Gi"},
        "components": {"api": {"cpu": "1000m", "memory": "1Gi", "replicas": 1},
                       "postgres": {"cpu": "500m", "memory": "1Gi", "replicas": 1}},
        "worker_cpu": "250m", "worker_policy": {"max_workers": 2, "largest_worker_mib": 512},
        "capacity": {"mixes": {"mixed": {"workflow": 1.0}}},
    }


def image_provenance(*, with_pod: bool) -> dict:
    provenance = {"tag": "bifrost-api:test", "host_id": "sha256:host",
                  "node": {"manifest_id": "sha256:manifest", "config_id": "sha256:config"},
                  "cri_config_id": "sha256:config"}
    if with_pod:
        provenance["api_pod"] = {"pod": "api-rotated-name", "pod_uid": "dynamic-uid",
                                  "image_id": "docker://sha256:config", "digest": "sha256:config"}
    return provenance


def confirmation(*, overall: str = "pass", rate: float = 10.0) -> dict:
    return {
        "overall": overall, "rate": rate,
        "verdict": {"latency": {"overall": "pass", "p99_s": 0.2}},
        "execution_rates": {"total": {"event_rates_per_s": {"correct_completed": 9.5}}},
        "resource_usage": {"roles": {"worker": {"max_interval_cpu_cores": 0.4}}},
        "storage": {"before": {"frames": ["before"]}, "after": {"frames": ["after"]}},
    }


def capacity(*, overall: str = "pass", rate: float = 10.0) -> dict:
    return {
        "kind": "capacity", "git_sha": "abc1234", "profile_name": "4gb-capacity", "mix_name": "mixed",
        "api_image": "bifrost-api:test", "api_image_provenance": image_provenance(with_pod=True),
        "sustainable_rate": rate if overall == "pass" else None,
        "confirmations": [confirmation(overall=overall, rate=rate)], "profile": profile(),
    }


def campaign() -> dict:
    manifest = {
        "profile_name": "4gb-capacity", "mix_name": "mixed", "mix": {"workflow": 1.0},
        "resolved_profile_sha256": digest(profile()), "harness_git_sha": "abc1234" + "0" * 33,
        "api_image": {"tag": "bifrost-api:test", "id": "sha256:image", "provenance": image_provenance(with_pod=False)},
        "rate_per_s": 2.236,
    }
    return {"kind": "mixed-confirmation-campaign", "campaign_manifest": manifest,
            "campaign_manifest_sha256": digest(manifest), "preflight": {"status": "pass"},
            "segments": [{"index": 0, "status": "pass"}], "overall": "pass"}


def soak() -> dict:
    manifest_hash = campaign()["campaign_manifest_sha256"]
    return {
        "kind": "soak", "git_sha": "abc1234", "profile_name": "4gb-capacity",
        "images": {"api": {"tag": "bifrost-api:test", "id": "sha256:image"}},
        "api_image_provenance": image_provenance(with_pod=True), "campaign_manifest_sha256": manifest_hash,
        "rate_per_s": 2.236 * 0.7, "status": "observational", "cleanup": {"verified": True},
        "growth_per_1000_terminal": {"db_bytes_per_1000_terminal": 1024**2,
                                       "object_bytes_per_1000_terminal": 1024**2,
                                       "wal_generated_bytes_per_1000_terminal": 7},
        "capacity_evidence": {"overall": "pass", "validity": {"invalid": []}},
        "object_storage": {"unmeasured": "cloud billing"},
    }


class HostingTest(unittest.TestCase):
    def scenario(self, directory: Path, *, node_cpu: str = "8", node_price: float | None = 40) -> dict:
        return {
            "evidence": {"capacity": write_reference(directory / "capacity.json", capacity()),
                         "campaign": write_reference(directory / "campaign.json", campaign()),
                         "soak": write_reference(directory / "soak.json", soak())},
            "node": {"allocatable": {"cpu": node_cpu, "memory": "16Gi"}, "other_requested": {"cpu": "0", "memory": "0"},
                     "reserve": {"cpu": "0", "memory": "0"}, "pods": 100, "pvcs": 20, "storage_gib": 1000},
            "ha_min_nodes": 2, "node_utilized_month_fraction": 1,
            "retention": {"initial_data_gib": 10, "retention_days": 30, "backup_copies": 1, "retained_versions": 1, "reserve_fraction": 0.2},
            "pricing": {"node": {"monthly_unit_price_usd": node_price}, "volume": {"per_provisioned_gib_month_usd": 0.1},
                        "kubernetes_control_plane_monthly_usd": 0,
                        "model_provider": {"workflow_per_success_usd": 0.01, "agent_per_success_usd": 0.02},
                        "object_storage": {"base_monthly_usd": 5, "stored_gib": 10, "included_gib": 250,
                                           "extra_gib_month_usd": 0.02, "outbound_gib": 0, "included_outbound_gib": 1024,
                                           "extra_outbound_gib_usd": 0.01, "request_fee_monthly_usd": 0},
                        "shared_infrastructure": {"build": 5, "observability": 3, "load_balancer": 12, "network": 1, "storage": 0}},
            "demand": {name: {"successful_workflows_per_month": 1000, "successful_agent_requests_per_month": 200,
                               "peak_rate_per_s": 3, "tenant_placements": 3, "revenue_monthly_usd": 200}
                       for name in ("low", "base", "peak")},
        }

    def test_actual_artifacts_keep_campaign_certification_and_soak_observation_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            card = hosting.build_card(self.scenario(Path(raw)))
        self.assertEqual(card["certification"]["status"], "certified")
        self.assertEqual(card["workload_rates"]["configured_offered_rate_per_s"], 10.0)
        self.assertEqual(card["workload_rates"]["measured_correct_rate_per_s"], 9.5)
        observed = card["resource_capacity"]["actual_observed"]
        self.assertEqual(observed["latency"]["p99_s"], 0.2)
        self.assertEqual(observed["soak_observation"]["status"], "observational")
        self.assertEqual(observed["soak_observation"]["generated_bytes_per_terminal"], 2 * 1024**2 / 1000)
        self.assertEqual(card["density"]["per_node"], 4)
        base = card["cost"]["by_demand"]["base"]
        self.assertEqual(base["nodes"], 2)
        self.assertEqual(base["node_cost_usd"], 80)
        self.assertEqual(base["kubernetes_control_plane_cost_usd"], 0)
        self.assertGreater(base["storage"]["provisioned_gib"], 10)

    def test_failed_capacity_is_not_certified_but_a_card_remains_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            scenario = self.scenario(directory, node_price=None)
            scenario["evidence"]["capacity"] = write_reference(directory / "failed.json", capacity(overall="fail"))
            card = hosting.build_card(scenario)
        self.assertEqual(card["certification"]["status"], "not_certified")
        self.assertIsNone(card["resource_capacity"]["actual_observed"]["execution_rates"])
        self.assertIsNone(card["cost"]["by_demand"]["base"]["node_cost_usd"])
        self.assertIn("pricing.node.monthly_unit_price_usd", card["cost"]["missing_inputs"])

    def test_unpinned_or_mismatched_actual_artifacts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            scenario = self.scenario(directory)
            del scenario["evidence"]["capacity"]["sha256"]
            with self.assertRaisesRegex(ValueError, "pin"):
                hosting.build_card(scenario)
            scenario = self.scenario(directory)
            invalid = campaign()
            invalid["campaign_manifest"]["resolved_profile_sha256"] = "0" * 64
            invalid["campaign_manifest_sha256"] = digest(invalid["campaign_manifest"])
            scenario["evidence"]["campaign"] = write_reference(directory / "mismatched-campaign.json", invalid)
            with self.assertRaisesRegex(ValueError, "profile hash"):
                hosting.build_card(scenario)
            scenario = self.scenario(directory)
            invalid = campaign()
            invalid["campaign_manifest"]["mix"] = {"workflow": 0.5, "agent": 0.5}
            invalid["campaign_manifest_sha256"] = digest(invalid["campaign_manifest"])
            scenario["evidence"]["campaign"] = write_reference(directory / "mismatched-mix.json", invalid)
            soak_artifact = soak()
            soak_artifact["campaign_manifest_sha256"] = invalid["campaign_manifest_sha256"]
            scenario["evidence"]["soak"] = write_reference(directory / "mismatched-mix-soak.json", soak_artifact)
            with self.assertRaisesRegex(ValueError, "workload mix"):
                hosting.build_card(scenario)

    def test_unknown_price_control_plane_and_object_request_fee_propagate_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            scenario = self.scenario(Path(raw))
            scenario["pricing"]["object_storage"]["request_fee_monthly_usd"] = None
            scenario["pricing"]["object_storage"]["guessed_request_rate"] = 123
            scenario["pricing"]["kubernetes_control_plane_monthly_usd"] = None
            scenario["pricing"]["unmodeled_charge"] = 99
            card = hosting.build_card(scenario)
        base = card["cost"]["by_demand"]["base"]
        self.assertIsNone(base["object_storage_cost_usd"])
        self.assertIsNone(base["kubernetes_control_plane_cost_usd"])
        self.assertIsNone(base["combined_cost_usd"])
        self.assertIn("pricing.kubernetes_control_plane_monthly_usd", card["cost"]["missing_inputs"])
        self.assertEqual(card["cost"]["unsupported_pricing_keys"], ["object_storage.guessed_request_rate", "unmodeled_charge"])

    def test_observed_node_capacity_can_make_profile_infeasible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            scenario = self.scenario(directory, node_cpu="3890m")
            artifact = capacity()
            artifact["profile"]["components"]["api"]["cpu"] = "2900m"
            scenario["evidence"]["capacity"] = write_reference(directory / "infeasible.json", artifact)
            invalid = campaign()
            invalid["campaign_manifest"]["resolved_profile_sha256"] = digest(artifact["profile"])
            invalid["campaign_manifest_sha256"] = digest(invalid["campaign_manifest"])
            scenario["evidence"]["campaign"] = write_reference(directory / "infeasible-campaign.json", invalid)
            soak_artifact = soak()
            soak_artifact["campaign_manifest_sha256"] = invalid["campaign_manifest_sha256"]
            scenario["evidence"]["soak"] = write_reference(directory / "infeasible-soak.json", soak_artifact)
            card = hosting.build_card(scenario)
        self.assertEqual(card["resource_capacity"]["max_component_requests"]["cpu_m"], 3900)
        self.assertEqual(card["density"]["per_node"], 0)
        self.assertIsNone(card["cost"]["by_demand"]["base"]["nodes"])

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
            with self.assertRaises(SystemExit):
                hosting.main()

    def test_gib_retention_stock_uses_per_terminal_growth_not_soak_duration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            scenario = self.scenario(Path(raw))
            scenario["retention"]["initial_data_gib"] = 1
            card = hosting.build_card(scenario)
        storage = card["cost"]["by_demand"]["base"]["storage"]
        # Two MiB per 1,000 terminal records x 1,200 monthly successes = 2.34375 GiB.
        self.assertAlmostEqual(storage["provisioned_gib"], (1 + 2 * 1200 / 1000 / 1024) * 3 * 1.2)
        self.assertIn("explicit per-terminal", storage["basis"])


if __name__ == "__main__":
    unittest.main()
