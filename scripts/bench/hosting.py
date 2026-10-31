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
import re
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


def _sha256_value(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_reference(value: object, label: str) -> tuple[dict[str, Any], dict[str, str]]:
    reference = _object(value, label)
    path = reference.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{label}.path is required")
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = reference.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError(f"{label}.sha256 must pin the immutable artifact")
    if expected != digest:
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


def _status(capacity: dict[str, Any], campaign: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    sustainable = capacity.get("sustainable_rate")
    if type(sustainable) not in (int, float) or sustainable <= 0:
        reasons.append("capacity artifact has no positive sustainable_rate")
    elif _confirmed_step(capacity) is None:
        reasons.append("capacity artifact has no passing confirmation at sustainable_rate")
    segments = campaign.get("segments")
    if not isinstance(segments, list) or not segments:
        reasons.append("campaign has no segment references")
    # Campaign manifests identify planned segments; a completed result must be
    # provided in the same object by the caller after campaign reconciliation.
    if campaign.get("overall") != "pass":
        reasons.append("campaign overall is not pass")
    return ("certified" if not reasons else "not_certified"), reasons


def _image_provenance(result: dict[str, Any], label: str) -> dict[str, Any]:
    # Capacity-search results retain this at the result root.  The
    # confirmation-only runner additionally snapshots it under each setup.
    provenance = result.get("api_image_provenance")
    if isinstance(provenance, dict):
        return provenance
    for setup_name in ("steady_setup", "confirmation_setup"):
        setup = result.get(setup_name)
        if isinstance(setup, dict) and isinstance(setup.get("api_image_provenance"), dict):
            return setup["api_image_provenance"]
    raise ValueError(f"{label} requires API image provenance captured by the harness")


def _image_tag(result: dict[str, Any], label: str) -> str:
    tag = result.get("api_image")
    if isinstance(tag, str) and tag:
        return tag
    images = _object(result.get("images"), f"{label}.images")
    api = _object(images.get("api"), f"{label}.images.api")
    tag = api.get("tag")
    if not isinstance(tag, str) or not tag:
        raise ValueError(f"{label} requires a selected API image tag")
    return tag


def _same_commit(left: object, right: object) -> bool:
    """Accept the harness's clean short/full SHA forms, never a dirty label."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if not re.fullmatch(r"[0-9a-f]{7,40}", left) or not re.fullmatch(r"[0-9a-f]{7,40}", right):
        return False
    return left.startswith(right) or right.startswith(left)


def _immutable_provenance(value: object, label: str) -> dict[str, Any]:
    provenance = _object(value, label)
    chain = _object(provenance.get("node"), f"{label}.node")
    immutable = {
        "tag": provenance.get("tag"), "host_id": provenance.get("host_id"),
        "node": chain, "cri_config_id": provenance.get("cri_config_id"),
    }
    if not all(immutable[key] for key in ("tag", "host_id", "cri_config_id")):
        raise ValueError(f"{label} lacks immutable API image-chain fields")
    api_pod = provenance.get("api_pod")
    if api_pod is not None:
        pod = _object(api_pod, f"{label}.api_pod")
        # Pod name/UID are intentionally dynamic.  The harness already proves
        # image_id is in its OCI chain; retain the stable digest-to-CRI check.
        if pod.get("digest") != immutable["cri_config_id"]:
            raise ValueError(f"{label}.api_pod digest does not match the selected CRI config")
    return immutable


def _matched_evidence(capacity: dict[str, Any], campaign: dict[str, Any], soak: dict[str, Any] | None) -> dict[str, Any]:
    manifest = _object(campaign.get("campaign_manifest"), "campaign.campaign_manifest")
    manifest_hash = campaign.get("campaign_manifest_sha256")
    if not isinstance(manifest_hash, str) or manifest_hash != _sha256_value(manifest):
        raise ValueError("campaign aggregate does not pin its immutable manifest")
    profile = capacity.get("profile_name")
    mix = capacity.get("mix_name")
    if not isinstance(profile, str) or not isinstance(mix, str):
        raise ValueError("capacity artifact requires profile_name and mix_name")
    if manifest.get("profile_name") != profile or manifest.get("mix_name") != mix:
        raise ValueError("capacity and campaign frozen profile/workload do not match")
    resolved_profile = _object(capacity.get("profile"), "capacity.profile")
    if manifest.get("resolved_profile_sha256") != _sha256_value(resolved_profile):
        raise ValueError("capacity profile does not match the campaign's frozen profile hash")
    profile_capacity = _object(resolved_profile.get("capacity"), "capacity.profile.capacity")
    mixes = _object(profile_capacity.get("mixes"), "capacity.profile.capacity.mixes")
    if mixes.get(mix) != manifest.get("mix"):
        raise ValueError("capacity workload mix does not match the campaign's frozen mix")
    if not _same_commit(capacity.get("git_sha"), manifest.get("harness_git_sha")):
        raise ValueError("capacity harness identity does not match the campaign")
    image = _object(manifest.get("api_image"), "campaign.campaign_manifest.api_image")
    manifest_provenance = _immutable_provenance(image.get("provenance"), "campaign API image provenance")
    if (_image_tag(capacity, "capacity") != image.get("tag")
            or _immutable_provenance(_image_provenance(capacity, "capacity"), "capacity API image provenance") != manifest_provenance):
        raise ValueError("capacity API image provenance does not match the campaign")
    if soak is not None:
        if soak.get("profile_name") != profile or soak.get("campaign_manifest_sha256") != manifest_hash:
            raise ValueError("soak frozen profile/workload does not match capacity")
        if not _same_commit(soak.get("git_sha"), manifest.get("harness_git_sha")) or _image_tag(soak, "soak") != image.get("tag"):
            raise ValueError("soak harness or API image identity does not match the campaign")
        soak_provenance = _immutable_provenance(_image_provenance(soak, "soak"), "soak API image provenance")
        if soak_provenance != manifest_provenance:
            raise ValueError("soak API image provenance does not match the campaign")
    rate = capacity.get("sustainable_rate")
    if type(rate) not in (int, float) or rate <= 0:
        rate = None
    return {"profile_name": profile, "mix_name": mix, "confirmed_rate_per_s": rate,
            "campaign_manifest_sha256": manifest_hash, "harness_git_sha": manifest["harness_git_sha"],
            "api_image": image}


def _confirmed_step(capacity: dict[str, Any]) -> dict[str, Any] | None:
    rate = capacity.get("sustainable_rate")
    if type(rate) not in (int, float) or rate <= 0:
        return None
    matches = [step for step in capacity.get("confirmations", [])
               if isinstance(step, dict) and step.get("overall") == "pass" and step.get("rate") == rate]
    return matches[0] if len(matches) == 1 else None


def _soak_observation(soak: dict[str, Any] | None) -> tuple[dict[str, Any] | None, float | None]:
    """Return descriptive soak evidence; it never upgrades capacity certification."""
    if soak is None:
        return None, None
    status = soak.get("status")
    cleanup = soak.get("cleanup")
    if status != "observational" or not isinstance(cleanup, dict) or cleanup.get("verified") is not True:
        return {"status": "invalid", "reason": "soak is not a completed observational run with verified cleanup"}, None
    growth = _object(soak.get("growth_per_1000_terminal"), "soak.growth_per_1000_terminal")
    db = growth.get("db_bytes_per_1000_terminal")
    objects = growth.get("object_bytes_per_1000_terminal")
    if (type(db) not in (int, float) or type(objects) not in (int, float)
            or not math.isfinite(float(db)) or not math.isfinite(float(objects)) or db < 0 or objects < 0):
        return {"status": "incomplete", "growth_per_1000_terminal": growth,
                "reason": "database/object growth is missing, non-finite, or negative; no long-run shrinkage assumption"}, None
    generated = (float(db) + float(objects)) / 1000
    return {"status": "observational", "growth_per_1000_terminal": growth,
            "generated_bytes_per_terminal": generated,
            "limit": "descriptive soak evidence; it does not certify capacity"}, generated


def _observed_rates(capacity: dict[str, Any]) -> dict[str, Any]:
    offered = capacity.get("sustainable_rate")
    if type(offered) not in (int, float) or offered <= 0:
        offered = None
    step = _confirmed_step(capacity)
    rates = step.get("execution_rates") if step else None
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


def _unsupported_pricing_keys(pricing: dict[str, Any]) -> list[str]:
    supported = {
        "node", "volume", "object_storage", "model_provider", "shared_infrastructure",
        "kubernetes_control_plane_monthly_usd", "kubernetes_control_plane_note", "kubernetes_source",
        "shared_infrastructure_source",
    }
    unsupported = {key for key in pricing if key not in supported}
    nested = {
        "node": {"monthly_unit_price_usd", "note", "source", "accessed"},
        "volume": {"per_provisioned_gib_month_usd", "note", "source", "accessed"},
        "object_storage": {"base_monthly_usd", "stored_gib", "included_gib", "extra_gib_month_usd",
                           "outbound_gib", "included_outbound_gib", "extra_outbound_gib_usd",
                           "request_fee_monthly_usd", "note", "source", "accessed"},
        "model_provider": {"workflow_per_success_usd", "agent_per_success_usd", "note", "source", "accessed"},
    }
    for section, allowed in nested.items():
        value = pricing.get(section)
        if isinstance(value, dict):
            unsupported.update(f"{section}.{key}" for key in value if key not in allowed)
    return sorted(unsupported)


def _costs(scenario: dict[str, Any], density: dict[str, Any], generated_bytes: float | None) -> dict[str, Any]:
    pricing = _object(scenario.get("pricing"), "pricing")
    demands = _object(scenario.get("demand"), "demand")
    retention = _object(scenario.get("retention"), "retention")
    missing: list[str] = []
    unsupported = _unsupported_pricing_keys(pricing)
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
        control_plane = pricing.get("kubernetes_control_plane_monthly_usd")
        if control_plane is None:
            control_plane_cost = None
            missing.append("pricing.kubernetes_control_plane_monthly_usd")
        else:
            control_plane_cost = _number(control_plane, "pricing.kubernetes_control_plane_monthly_usd")
        combined = _nullable_sum([node_cost, volume_cost, object_storage_cost, model_cost, shared_cost, control_plane_cost])
        revenue = demand.get("revenue_monthly_usd")
        margin = None if revenue is None or _number(revenue, f"demand.{name}.revenue_monthly_usd") <= 0 or combined is None else _number(revenue, f"demand.{name}.revenue_monthly_usd") - combined
        margin_percent = None if margin is None else margin / _number(revenue, f"demand.{name}.revenue_monthly_usd") * 100
        results[name] = {"successful_workflows_per_month": workflows, "successful_agent_requests_per_month": agents,
                         "peak_rate_per_s": demand["peak_rate_per_s"], "tenant_placements": placements, "nodes": nodes,
                         "node_cost_usd": node_cost, "storage": {"provisioned_gib": provisioned, "volume_cost_usd": volume_cost,
                         "basis": "initial data plus explicit per-terminal generation and retention/backup/version reserves; not extrapolated from a two-hour slope"},
                         "model_provider_cost_usd": model_cost, "shared_infrastructure_cost_usd": shared_cost,
                         "object_storage_cost_usd": object_storage_cost,
                         "kubernetes_control_plane_cost_usd": control_plane_cost,
                         "combined_cost_usd": combined, "revenue_monthly_usd": revenue, "margin_usd": margin}
        results[name]["margin_percent"] = margin_percent
    return {"by_demand": results, "missing_inputs": sorted(set(missing)), "unsupported_pricing_keys": unsupported,
            "node_billing_basis": "Ready node time: monthly node price × node count × utilized month fraction, never pod utilization"}


def build_card(scenario: dict[str, Any]) -> dict[str, Any]:
    evidence = _object(scenario.get("evidence"), "evidence")
    capacity, capacity_ref = _load_reference(evidence.get("capacity"), "evidence.capacity")
    campaign, campaign_ref = _load_reference(evidence.get("campaign"), "evidence.campaign")
    soak_ref = None
    soak = None
    if evidence.get("soak") is not None:
        soak, soak_ref = _load_reference(evidence["soak"], "evidence.soak")
    matched = _matched_evidence(capacity, campaign, soak)
    certification, reasons = _status(capacity, campaign)
    tenant, pods, pvcs, storage_gib = _profile_occupancy(_object(capacity.get("profile"), "capacity.profile"))
    step = _confirmed_step(capacity)
    soak_observation, generated = _soak_observation(soak)
    density = _density(scenario, tenant, pods, pvcs, storage_gib)
    return {"schema_version": 1, "kind": "hosting-capacity-card", "evidence": {"capacity": capacity_ref, "campaign": campaign_ref, "soak": soak_ref},
            "certification": {"status": certification, "reasons": reasons}, "frozen": matched,
            "resource_capacity": {"requested_quota": _object(capacity["profile"]["quota"], "capacity.profile.quota"),
                                  "max_component_requests": {"cpu_m": tenant.cpu_m, "memory_bytes": tenant.memory, "pods": pods, "pvcs": pvcs, "storage_gib": storage_gib},
                                  "actual_observed": {"resource_usage": step.get("resource_usage") if step else None,
                                                      "latency": _object(step.get("verdict"), "confirmation.verdict").get("latency") if step else None,
                                                      "execution_rates": step.get("execution_rates") if step else None,
                                                      "storage_collector_frames": step.get("storage") if step else None,
                                                      "soak_observation": soak_observation,
                                                      "recovery_limitations": None if soak is None else {
                                                          "capacity_evidence": soak.get("capacity_evidence"),
                                                          "object_storage": soak.get("object_storage"),
                                                      }}},
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
