#!/usr/bin/env python3
"""Offline, evidence-bound hosting capacity and cost cards.

The calculator intentionally does not discover prices, estimate customer demand
from a saturation test, or mutate benchmark evidence.  Its scenario is the
complete set of commercial assumptions; benchmark artifacts contribute only
their measured facts and must describe one frozen profile/workload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from envelope import Resources, parse_cpu, parse_memory


GIB = 1024**3
DAYS_PER_MONTH = 30


def _number(value: object, label: str, *, nonnegative: bool = True) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if nonnegative and value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _load_reference(value: object, label: str) -> tuple[dict[str, Any], dict[str, str]]:
    reference = _object(value, label)
    path = reference.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{label}.path is required")
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = reference.get("sha256")
    if expected is not None and expected != digest:
        raise ValueError(f"{label} sha256 does not match")
    try:
        loaded = json.loads(data)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not JSON") from error
    return _object(loaded, label), {"path": path, "sha256": digest}


def _resources(value: object, label: str) -> Resources:
    item = _object(value, label)
    try:
        return Resources(parse_cpu(item["cpu"]), parse_memory(item["memory"]))
    except (KeyError, ValueError) as error:
        raise ValueError(f"{label} needs Kubernetes cpu and memory quantities") from error


def _profile_occupancy(profile: dict[str, Any]) -> tuple[Resources, int, int, float]:
    components = _object(profile.get("components"), "capacity profile.components")
    total = Resources(0, 0)
    for name, component in components.items():
        item = _object(component, f"component {name}")
        replicas = int(_number(item.get("replicas", 1), f"component {name}.replicas"))
        if replicas < 1:
            raise ValueError(f"component {name}.replicas must be positive")
        total += Resources(parse_cpu(item["cpu"]), parse_memory(item["memory"])) * replicas
    policy = _object(profile.get("worker_policy"), "capacity profile.worker_policy")
    workers = int(_number(policy.get("max_workers"), "worker_policy.max_workers"))
    if workers < 0:
        raise ValueError("worker_policy.max_workers must not be negative")
    total += Resources(parse_cpu(profile["worker_cpu"]), int(_number(policy["largest_worker_mib"], "worker_policy.largest_worker_mib")) * 1024**2) * workers
    quota = _object(profile.get("quota"), "capacity profile.quota")
    storage_bytes = parse_memory(quota["storage"])
    return total, int(_number(quota["pods"], "quota.pods")), int(_number(quota["pvcs"], "quota.pvcs")), storage_bytes / GIB


def _status(capacity: dict[str, Any], campaign: dict[str, Any], soak: dict[str, Any] | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    confirmations = capacity.get("confirmations")
    sustainable = capacity.get("sustainable_rate")
    if not isinstance(confirmations, list) or not any(item.get("overall") == "pass" for item in confirmations if isinstance(item, dict)):
        reasons.append("capacity artifact has no passing confirmation")
    if type(sustainable) not in (int, float) or sustainable <= 0:
        reasons.append("capacity artifact has no positive sustainable_rate")
    segments = campaign.get("segments")
    if not isinstance(segments, list) or not segments:
        reasons.append("campaign has no segment references")
    # Campaign manifests identify planned segments; a completed result must be
    # provided in the same object by the caller after campaign reconciliation.
    if campaign.get("overall") != "pass":
        reasons.append("campaign overall is not pass")
    if soak is None:
        reasons.append("soak evidence is missing")
    elif soak.get("overall") != "pass":
        reasons.append("soak overall is not pass")
    return ("certified" if not reasons else "not_certified"), reasons


def _matched_evidence(capacity: dict[str, Any], campaign: dict[str, Any], soak: dict[str, Any] | None) -> dict[str, Any]:
    profile = capacity.get("profile_name")
    mix = capacity.get("mix_name")
    if not isinstance(profile, str) or not isinstance(mix, str):
        raise ValueError("capacity artifact requires profile_name and mix_name")
    if campaign.get("profile_name") != profile or campaign.get("mix_name") != mix:
        raise ValueError("capacity and campaign frozen profile/workload do not match")
    rate = capacity.get("sustainable_rate")
    if type(rate) not in (int, float) or rate <= 0:
        rate = None
    if soak is not None:
        if soak.get("profile_name") != profile or soak.get("mix_name") != mix:
            raise ValueError("soak frozen profile/workload does not match capacity")
        if rate is not None and soak.get("rate_per_s") != rate * 0.7:
            raise ValueError("soak rate is not 70% of the confirmed capacity rate")
    return {"profile_name": profile, "mix_name": mix, "confirmed_rate_per_s": rate}


def _observed_rates(capacity: dict[str, Any]) -> dict[str, Any]:
    offered = capacity.get("sustainable_rate")
    if type(offered) not in (int, float) or offered <= 0:
        offered = None
    rates = capacity.get("execution_rates")
    if not isinstance(rates, dict):
        return {"configured_offered_rate_per_s": offered, "measured_correct_rate_per_s": None,
                "limit": "execution_rates missing; offered rate is not measured correct throughput"}
    total = _object(rates.get("total"), "execution_rates.total")
    events = _object(total.get("event_rates_per_s"), "execution_rates.total.event_rates_per_s")
    return {"configured_offered_rate_per_s": offered,
            "measured_correct_rate_per_s": events.get("correct_completed"),
            "limit": "correct completions are an observed event rate; offered load remains separate"}


def _density(scenario: dict[str, Any], tenant: Resources, tenant_pods: int, tenant_pvcs: int, tenant_storage_gib: float) -> dict[str, Any]:
    node = _object(scenario.get("node"), "node")
    allocatable = _resources(node.get("allocatable"), "node.allocatable")
    other = _resources(node.get("other_requested", {"cpu": "0", "memory": "0"}), "node.other_requested")
    reserve = _resources(node.get("reserve", {"cpu": "0", "memory": "0"}), "node.reserve")
    available = Resources(allocatable.cpu_m - other.cpu_m - reserve.cpu_m, allocatable.memory - other.memory - reserve.memory)
    limits = [available.cpu_m // tenant.cpu_m if tenant.cpu_m else math.inf,
              available.memory // tenant.memory if tenant.memory else math.inf]
    for key, denominator in (("pods", tenant_pods), ("pvcs", tenant_pvcs), ("storage_gib", tenant_storage_gib)):
        if denominator:
            if key not in node:
                raise ValueError(f"node.{key} is required for nonzero tenant {key}")
            available_count = (_number(node[key], f"node.{key}")
                               - _number(node.get(f"other_{key}", 0), f"node.other_{key}")
                               - _number(node.get(f"reserve_{key}", 0), f"node.reserve_{key}"))
            limits.append(math.floor(available_count / denominator))
    density = max(0, int(min(limits)))
    return {"per_node": density, "available_after_other_and_reserve": {"cpu_m": available.cpu_m, "memory_bytes": available.memory},
            "tenant_max_requests": {"cpu_m": tenant.cpu_m, "memory_bytes": tenant.memory, "pods": tenant_pods, "pvcs": tenant_pvcs, "storage_gib": tenant_storage_gib},
            "status": "feasible" if density else "infeasible"}


def _nullable_sum(values: list[float | None]) -> float | None:
    return None if any(value is None for value in values) else sum(value for value in values if value is not None)


def _costs(scenario: dict[str, Any], density: dict[str, Any], generated_bytes: float | None) -> dict[str, Any]:
    pricing = _object(scenario.get("pricing"), "pricing")
    demands = _object(scenario.get("demand"), "demand")
    retention = _object(scenario.get("retention"), "retention")
    missing: list[str] = []
    results: dict[str, Any] = {}
    for name in ("low", "base", "peak"):
        demand = _object(demands.get(name), f"demand.{name}")
        workflows = _number(demand.get("successful_workflows_per_month"), f"demand.{name}.successful_workflows_per_month")
        agents = _number(demand.get("successful_agent_requests_per_month"), f"demand.{name}.successful_agent_requests_per_month")
        placements = int(_number(demand.get("tenant_placements"), f"demand.{name}.tenant_placements"))
        _number(demand.get("peak_rate_per_s"), f"demand.{name}.peak_rate_per_s")
        nodes = None if density["per_node"] == 0 else max(int(_number(scenario.get("ha_min_nodes", 1), "ha_min_nodes")), math.ceil(placements / density["per_node"]))
        node_price = _object(pricing.get("node", {}), "pricing.node").get("monthly_unit_price_usd")
        node_cost = None if nodes is None or node_price is None else nodes * _number(node_price, "pricing.node.monthly_unit_price_usd") * _number(scenario.get("node_utilized_month_fraction", 1), "node_utilized_month_fraction")
        if node_price is None:
            missing.append("pricing.node.monthly_unit_price_usd")
        initial = _number(retention.get("initial_data_gib"), "retention.initial_data_gib")
        days = _number(retention.get("retention_days"), "retention.retention_days")
        copies = _number(retention.get("backup_copies", 0), "retention.backup_copies")
        versions = _number(retention.get("retained_versions", 0), "retention.retained_versions")
        reserve = _number(retention.get("reserve_fraction", 0), "retention.reserve_fraction")
        growth = None if generated_bytes is None else generated_bytes * (workflows + agents) * days / DAYS_PER_MONTH / GIB
        provisioned = None if growth is None else (initial + growth) * (1 + copies + versions) * (1 + reserve)
        volume_price = _object(pricing.get("volume", {}), "pricing.volume").get("per_provisioned_gib_month_usd")
        volume_cost = None if provisioned is None or volume_price is None else provisioned * _number(volume_price, "pricing.volume.per_provisioned_gib_month_usd")
        if generated_bytes is None:
            missing.append("soak.generated_bytes_per_terminal")
        if volume_price is None:
            missing.append("pricing.volume.per_provisioned_gib_month_usd")
        model = _object(pricing.get("model_provider", {}), "pricing.model_provider")
        wp, ap = model.get("workflow_per_success_usd"), model.get("agent_per_success_usd")
        model_cost = None if wp is None or ap is None else workflows * _number(wp, "pricing.model_provider.workflow_per_success_usd") + agents * _number(ap, "pricing.model_provider.agent_per_success_usd")
        if wp is None:
            missing.append("pricing.model_provider.workflow_per_success_usd")
        if ap is None:
            missing.append("pricing.model_provider.agent_per_success_usd")
        object_storage_cost = 0.0
        object_storage = pricing.get("object_storage")
        if object_storage is not None:
            object_storage = _object(object_storage, "pricing.object_storage")
            request_fee = object_storage.get("request_fee_monthly_usd")
            required = ("base_monthly_usd", "stored_gib", "included_gib", "extra_gib_month_usd",
                        "outbound_gib", "included_outbound_gib", "extra_outbound_gib_usd")
            if request_fee is None or any(key not in object_storage for key in required):
                object_storage_cost = None
                missing.append("pricing.object_storage request or usage inputs")
            else:
                object_storage_cost = (
                    _number(object_storage["base_monthly_usd"], "pricing.object_storage.base_monthly_usd")
                    + max(0.0, _number(object_storage["stored_gib"], "pricing.object_storage.stored_gib")
                          - _number(object_storage["included_gib"], "pricing.object_storage.included_gib"))
                    * _number(object_storage["extra_gib_month_usd"], "pricing.object_storage.extra_gib_month_usd")
                    + max(0.0, _number(object_storage["outbound_gib"], "pricing.object_storage.outbound_gib")
                          - _number(object_storage["included_outbound_gib"], "pricing.object_storage.included_outbound_gib"))
                    * _number(object_storage["extra_outbound_gib_usd"], "pricing.object_storage.extra_outbound_gib_usd")
                    + _number(request_fee, "pricing.object_storage.request_fee_monthly_usd")
                )
        shared = _object(pricing.get("shared_infrastructure", {}), "pricing.shared_infrastructure")
        shared_cost = _nullable_sum([_number(value, f"pricing.shared_infrastructure.{key}") if value is not None else None for key, value in shared.items()])
        if not shared:
            missing.append("pricing.shared_infrastructure (build/observability/LB/network/storage categories)")
        combined = _nullable_sum([node_cost, volume_cost, object_storage_cost, model_cost, shared_cost])
        revenue = demand.get("revenue_monthly_usd")
        margin = None if revenue is None or _number(revenue, f"demand.{name}.revenue_monthly_usd") <= 0 or combined is None else _number(revenue, f"demand.{name}.revenue_monthly_usd") - combined
        margin_percent = None if margin is None else margin / _number(revenue, f"demand.{name}.revenue_monthly_usd") * 100
        results[name] = {"successful_workflows_per_month": workflows, "successful_agent_requests_per_month": agents,
                         "peak_rate_per_s": demand["peak_rate_per_s"], "tenant_placements": placements, "nodes": nodes,
                         "node_cost_usd": node_cost, "storage": {"provisioned_gib": provisioned, "volume_cost_usd": volume_cost,
                         "basis": "initial data plus explicit per-terminal generation and retention/backup/version reserves; not extrapolated from a two-hour slope"},
                         "model_provider_cost_usd": model_cost, "shared_infrastructure_cost_usd": shared_cost,
                         "object_storage_cost_usd": object_storage_cost,
                         "combined_cost_usd": combined, "revenue_monthly_usd": revenue, "margin_usd": margin}
        results[name]["margin_percent"] = margin_percent
    return {"by_demand": results, "missing_inputs": sorted(set(missing)), "node_billing_basis": "Ready node time: monthly node price × node count × utilized month fraction, never pod utilization"}


def build_card(scenario: dict[str, Any]) -> dict[str, Any]:
    evidence = _object(scenario.get("evidence"), "evidence")
    capacity, capacity_ref = _load_reference(evidence.get("capacity"), "evidence.capacity")
    campaign, campaign_ref = _load_reference(evidence.get("campaign"), "evidence.campaign")
    soak_ref = None
    soak = None
    if evidence.get("soak") is not None:
        soak, soak_ref = _load_reference(evidence["soak"], "evidence.soak")
    matched = _matched_evidence(capacity, campaign, soak)
    certification, reasons = _status(capacity, campaign, soak)
    tenant, pods, pvcs, storage_gib = _profile_occupancy(_object(capacity.get("profile"), "capacity.profile"))
    resources = capacity.get("resource_summary")
    storage = soak.get("storage") if soak else None
    generated = storage.get("generated_bytes_per_terminal") if isinstance(storage, dict) else None
    if generated is not None:
        generated = _number(generated, "soak.storage.generated_bytes_per_terminal")
    density = _density(scenario, tenant, pods, pvcs, storage_gib)
    return {"schema_version": 1, "kind": "hosting-capacity-card", "evidence": {"capacity": capacity_ref, "campaign": campaign_ref, "soak": soak_ref},
            "certification": {"status": certification, "reasons": reasons}, "frozen": matched,
            "resource_capacity": {"requested_quota": _object(capacity["profile"]["quota"], "capacity.profile.quota"),
                                  "max_component_requests": {"cpu_m": tenant.cpu_m, "memory_bytes": tenant.memory, "pods": pods, "pvcs": pvcs, "storage_gib": storage_gib},
                                  "actual_observed": {"resource_summary": resources, "latency": capacity.get("latency"),
                                                      "execution_rates": capacity.get("execution_rates"), "storage": storage,
                                                      "recovery_limitations": soak.get("recovery") if soak else None}},
            "workload_rates": _observed_rates(capacity), "density": density,
            "cost": _costs(scenario, density, generated),
            "limitations": ["Node allocatable resources are observed input, not advertised SKU capacity.",
                            "A zero density is infeasible and never produces a zero-cost hosting claim.",
                            "Unknown prices and incomplete evidence remain null rather than zero."]}


def markdown(card: dict[str, Any]) -> str:
    frozen = card["frozen"]
    lines = ["# Hosting Capacity Card", "", f"Certification: **{card['certification']['status']}**", "",
             f"Frozen profile/workload: `{frozen['profile_name']}` / `{frozen['mix_name']}`", "",
             "## Resource Capacity", "", "```json", json.dumps(card["resource_capacity"], indent=2, sort_keys=True), "```", "",
             "## Demand Costs", "", "```json", json.dumps(card["cost"], indent=2, sort_keys=True), "```"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("output directory must be fresh")
    scenario = _object(json.loads(args.scenario.read_text()), "scenario")
    card = build_card(scenario)
    args.out.mkdir(parents=True)
    (args.out / "capacity-card.json").write_text(json.dumps(card, indent=2, sort_keys=True) + "\n")
    (args.out / "capacity-card.md").write_text(markdown(card))


if __name__ == "__main__":
    main()
