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


def _postgres_pvc_gib(profile: dict[str, Any]) -> float:
    components = _object(profile.get("components"), "capacity profile.components")
    postgres = _object(components.get("postgres"), "capacity profile.components.postgres")
    try:
        return parse_memory(postgres["storage"]) / GIB
    except (KeyError, ValueError) as error:
        raise ValueError("capacity profile.components.postgres.storage is required") from error


def _status(campaign: dict[str, Any], capacity_reasons: list[str] | None = None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if campaign.get("overall") != "pass":
        reasons.append("campaign overall is not pass")
    reasons.extend(capacity_reasons or [])
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


def _campaign_steps(campaign: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only the campaign's five aggregate-validated segment measurements."""
    segments = campaign.get("segments")
    if not isinstance(segments, list) or len(segments) != 5:
        raise ValueError("campaign aggregate must retain exactly five segments")
    if campaign.get("overall") != "pass":
        raise ValueError("campaign aggregate is not passing")
    if [segment.get("index") for segment in segments if isinstance(segment, dict)] != list(range(5)):
        raise ValueError("campaign segments must retain ordered indices 0 through 4")
    rate = manifest.get("rate_per_s")
    if type(rate) not in (int, float) or rate <= 0:
        raise ValueError("campaign manifest has no positive fixed offered rate")
    steps: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("status") != "pass":
            raise ValueError("campaign aggregate has a nonpassing segment")
        step = _object(segment.get("step"), "campaign segment.step")
        if step.get("overall") != "pass" or step.get("rate") != rate:
            raise ValueError("campaign segment does not match the passing fixed-rate aggregate")
        steps.append(step)
    pooled = _object(campaign.get("pooled_reliability"), "campaign.pooled_reliability")
    if not pooled or any(not isinstance(item, dict) or item.get("status") != "pass" for item in pooled.values()):
        raise ValueError("campaign pooled reliability is not passing")
    return steps


def _matched_evidence(capacity: dict[str, Any] | None, campaign: dict[str, Any], soak: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = _object(campaign.get("campaign_manifest"), "campaign.campaign_manifest")
    manifest_hash = campaign.get("campaign_manifest_sha256")
    if not isinstance(manifest_hash, str) or manifest_hash != _sha256_value(manifest):
        raise ValueError("campaign aggregate does not pin its immutable manifest")
    profile = manifest.get("profile_name")
    mix = manifest.get("mix_name")
    if not isinstance(profile, str) or not isinstance(mix, str):
        raise ValueError("campaign manifest requires profile_name and mix_name")
    resolved_profile = _object(manifest.get("resolved_profile"), "campaign.campaign_manifest.resolved_profile")
    if manifest.get("resolved_profile_sha256") != _sha256_value(resolved_profile):
        raise ValueError("campaign embedded resolved profile does not match its frozen hash")
    profile_capacity = _object(resolved_profile.get("capacity"), "capacity.profile.capacity")
    mixes = _object(profile_capacity.get("mixes"), "capacity.profile.capacity.mixes")
    if mixes.get(mix) != manifest.get("mix"):
        raise ValueError("campaign workload mix does not match its frozen profile")
    image = _object(manifest.get("api_image"), "campaign.campaign_manifest.api_image")
    manifest_provenance = _immutable_provenance(image.get("provenance"), "campaign API image provenance")
    steps = _campaign_steps(campaign, manifest)
    if capacity is not None:
        if (capacity.get("profile_name") != profile or capacity.get("mix_name") != mix
                or _object(capacity.get("profile"), "capacity.profile") != resolved_profile):
            raise ValueError("standalone capacity frozen profile/workload does not match the campaign")
        if capacity.get("sustainable_rate") != manifest.get("rate_per_s"):
            raise ValueError("standalone capacity sustainable rate does not match the campaign fixed offered rate")
        if _confirmed_step(capacity) is None or not _same_commit(capacity.get("git_sha"), manifest.get("harness_git_sha")):
            raise ValueError("standalone capacity lacks matching passing confirmation or harness identity")
        if (_image_tag(capacity, "capacity") != image.get("tag")
                or _immutable_provenance(_image_provenance(capacity, "capacity"), "capacity API image provenance") != manifest_provenance):
            raise ValueError("standalone capacity API image provenance does not match the campaign")
    if soak is not None:
        if soak.get("profile_name") != profile or soak.get("campaign_manifest_sha256") != manifest_hash:
            raise ValueError("soak frozen profile/workload does not match campaign")
        if not _same_commit(soak.get("git_sha"), manifest.get("harness_git_sha")) or _image_tag(soak, "soak") != image.get("tag"):
            raise ValueError("soak harness or API image identity does not match the campaign")
        soak_provenance = _immutable_provenance(_image_provenance(soak, "soak"), "soak API image provenance")
        if soak_provenance != manifest_provenance:
            raise ValueError("soak API image provenance does not match the campaign")
    return {"profile_name": profile, "mix_name": mix, "confirmed_rate_per_s": manifest["rate_per_s"],
            "campaign_manifest_sha256": manifest_hash, "harness_git_sha": manifest["harness_git_sha"],
            "api_image": image}, resolved_profile, steps


def _confirmed_step(capacity: dict[str, Any]) -> dict[str, Any] | None:
    rate = capacity.get("sustainable_rate")
    if type(rate) not in (int, float) or rate <= 0:
        return None
    matches = [step for step in capacity.get("confirmations", [])
               if isinstance(step, dict) and step.get("overall") == "pass" and step.get("rate") == rate]
    return matches[0] if len(matches) == 1 else None


def _soak_observation(soak: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, float] | None]:
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
    generated = {"database_bytes_per_terminal": float(db) / 1000,
                 "object_bytes_per_terminal": float(objects) / 1000}
    return {"status": "observational", "growth_per_1000_terminal": growth,
            "database_generated_bytes_per_terminal": generated["database_bytes_per_terminal"],
            "object_generated_bytes_per_terminal": generated["object_bytes_per_terminal"],
            "limit": "descriptive soak evidence; it does not certify capacity"}, generated


def _observed_rates(campaign: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = _object(campaign["campaign_manifest"], "campaign.campaign_manifest")
    weighted, duration, by_segment = 0.0, 0.0, []
    for index, step in enumerate(steps):
        rate = None
        rates = step.get("execution_rates")
        if isinstance(rates, dict) and isinstance(rates.get("total"), dict):
            events = rates["total"].get("event_rates_per_s")
            if isinstance(events, dict) and type(events.get("correct_completed")) in (int, float):
                rate = float(events["correct_completed"])
        window = step.get("window_s")
        usable = type(window) in (int, float) and window > 0 and rate is not None
        if usable:
            weighted += rate * float(window)
            duration += float(window)
        by_segment.append({"index": index, "window_s": window, "measured_correct_rate_per_s": rate})
    return {"configured_offered_rate_per_s": manifest["rate_per_s"],
            "measured_correct_rate_per_s": None if not duration else weighted / duration,
            "measurement_duration_s": duration, "by_segment": by_segment,
            "limit": "duration-weighted correct completions across the five passing campaign segments; offered load remains separate"}


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
        "backup",
        "kubernetes_control_plane_monthly_usd", "kubernetes_control_plane_note", "kubernetes_source",
        "shared_infrastructure_source",
    }
    unsupported = {key for key in pricing if key not in supported}
    nested = {
        "node": {"monthly_unit_price_usd", "note", "source", "accessed"},
        "volume": {"per_provisioned_gib_month_usd", "note", "source", "accessed"},
        "backup": {"per_retained_gib_month_usd", "note", "source", "accessed"},
        "object_storage": {"base_monthly_usd", "included_gib", "extra_gib_month_usd",
                           "outbound_gib", "included_outbound_gib", "extra_outbound_gib_usd",
                           "request_fee_monthly_usd", "note", "source", "accessed"},
        "model_provider": {"workflow_per_success_usd", "agent_per_success_usd", "note", "source", "accessed"},
        "shared_infrastructure": {"build", "observability", "regional_http_load_balancer", "network_load_balancer",
                                  "load_balancer", "network", "storage", "note", "source", "accessed"},
    }
    for section, allowed in nested.items():
        value = pricing.get(section)
        if isinstance(value, dict):
            unsupported.update(f"{section}.{key}" for key in value if key not in allowed)
    return sorted(unsupported)


def _costs(scenario: dict[str, Any], density: dict[str, Any], generated: dict[str, float] | None,
           postgres_pvc_gib: float, confirmed_rate: float | None) -> dict[str, Any]:
    pricing = _object(scenario.get("pricing"), "pricing")
    demands = _object(scenario.get("demand"), "demand")
    retention = _object(scenario.get("retention"), "retention")
    missing: list[str] = []
    unsupported = _unsupported_pricing_keys(pricing)
    demand_basis = retention.get("demand_basis")
    if demand_basis not in {"fleet", "per_tenant"}:
        raise ValueError("retention.demand_basis must be fleet or per_tenant")
    initial_database = _number(retention.get("initial_database_data_gib_per_tenant"), "retention.initial_database_data_gib_per_tenant")
    initial_object = _number(retention.get("initial_object_data_gib_per_tenant"), "retention.initial_object_data_gib_per_tenant")
    volume_per_tenant = _number(retention.get("database_volume_provisioned_gib_per_tenant"), "retention.database_volume_provisioned_gib_per_tenant")
    volume_minimum_per_tenant = _number(retention.get("database_volume_minimum_gib_per_tenant"), "retention.database_volume_minimum_gib_per_tenant")
    if volume_minimum_per_tenant < postgres_pvc_gib:
        raise ValueError("retention.database_volume_minimum_gib_per_tenant must cover declared PostgreSQL PVC storage")
    if volume_per_tenant < volume_minimum_per_tenant:
        raise ValueError("retention.database_volume_provisioned_gib_per_tenant is below its explicit minimum")
    backup_retained_per_tenant = _number(retention.get("backup_retained_gib_per_tenant"), "retention.backup_retained_gib_per_tenant")
    object_versions_per_tenant = _number(retention.get("object_versions_retained_gib_per_tenant"), "retention.object_versions_retained_gib_per_tenant")
    days = _number(retention.get("retention_days"), "retention.retention_days")
    reserve = _number(retention.get("reserve_fraction"), "retention.reserve_fraction")
    ready_fraction = _number(scenario.get("ready_node_month_fraction"), "ready_node_month_fraction")
    if ready_fraction > 1:
        raise ValueError("ready_node_month_fraction must be between 0 and 1")
    headroom = _number(scenario.get("confirmed_rate_utilization_fraction"), "confirmed_rate_utilization_fraction")
    if headroom > 1:
        raise ValueError("confirmed_rate_utilization_fraction must be between 0 and 1")
    results: dict[str, Any] = {}
    capacity_reasons: list[str] = []
    for name in ("low", "base", "peak"):
        demand = _object(demands.get(name), f"demand.{name}")
        workflows = _number(demand.get("successful_workflows_per_month"), f"demand.{name}.successful_workflows_per_month")
        agents = _number(demand.get("successful_agent_requests_per_month"), f"demand.{name}.successful_agent_requests_per_month")
        placements = int(_number(demand.get("tenant_placements"), f"demand.{name}.tenant_placements"))
        peak_rate = _number(demand.get("peak_rate_per_s"), f"demand.{name}.peak_rate_per_s")
        fleet_factor = placements if demand_basis == "per_tenant" else 1
        fleet_workflows, fleet_agents = workflows * fleet_factor, agents * fleet_factor
        fleet_terminals = fleet_workflows + fleet_agents
        ceiling = None if confirmed_rate is None else confirmed_rate * headroom
        capacity_status = "capacity_feasible" if ceiling is not None and peak_rate <= ceiling else "capacity_infeasible"
        if capacity_status == "capacity_infeasible":
            capacity_reasons.append(f"peak demand exceeds explicit headroom ceiling for {name}")
        nodes = None if density["per_node"] == 0 else max(int(_number(scenario.get("ha_min_nodes", 1), "ha_min_nodes")), math.ceil(placements / density["per_node"]))
        node_price = _object(pricing.get("node", {}), "pricing.node").get("monthly_unit_price_usd")
        node_cost = None if nodes is None or node_price is None else nodes * _number(node_price, "pricing.node.monthly_unit_price_usd") * ready_fraction
        if node_price is None:
            missing.append("pricing.node.monthly_unit_price_usd")
        database_growth = None if generated is None else generated["database_bytes_per_terminal"] * fleet_terminals * days / DAYS_PER_MONTH / GIB
        object_growth = None if generated is None else generated["object_bytes_per_terminal"] * fleet_terminals * days / DAYS_PER_MONTH / GIB
        database_logical_stock = None if database_growth is None else (placements * initial_database + database_growth) * (1 + reserve)
        object_stored = None if object_growth is None else (placements * (initial_object + object_versions_per_tenant) + object_growth) * (1 + reserve)
        provisioned = placements * volume_per_tenant
        minimum = placements * volume_minimum_per_tenant
        backup_retained = placements * backup_retained_per_tenant
        volume_price = _object(pricing.get("volume", {}), "pricing.volume").get("per_provisioned_gib_month_usd")
        volume_cost = None if provisioned is None or volume_price is None else provisioned * _number(volume_price, "pricing.volume.per_provisioned_gib_month_usd")
        if generated is None:
            missing.extend(["soak.database_generated_bytes_per_terminal", "soak.object_generated_bytes_per_terminal"])
        if volume_price is None:
            missing.append("pricing.volume.per_provisioned_gib_month_usd")
        backup = pricing.get("backup")
        backup_price = None if not isinstance(backup, dict) else backup.get("per_retained_gib_month_usd")
        backup_cost = None if backup_price is None else backup_retained * _number(backup_price, "pricing.backup.per_retained_gib_month_usd")
        if backup_price is None:
            missing.append("pricing.backup.per_retained_gib_month_usd")
        model = _object(pricing.get("model_provider", {}), "pricing.model_provider")
        wp, ap = model.get("workflow_per_success_usd"), model.get("agent_per_success_usd")
        model_cost = None if wp is None or ap is None else fleet_workflows * _number(wp, "pricing.model_provider.workflow_per_success_usd") + fleet_agents * _number(ap, "pricing.model_provider.agent_per_success_usd")
        if wp is None:
            missing.append("pricing.model_provider.workflow_per_success_usd")
        if ap is None:
            missing.append("pricing.model_provider.agent_per_success_usd")
        object_storage_cost = None
        object_storage = pricing.get("object_storage")
        if object_storage is None:
            missing.append("pricing.object_storage")
        else:
            object_storage = _object(object_storage, "pricing.object_storage")
            request_fee = object_storage.get("request_fee_monthly_usd")
            required = ("base_monthly_usd", "included_gib", "extra_gib_month_usd",
                        "outbound_gib", "included_outbound_gib", "extra_outbound_gib_usd")
            if object_stored is None or request_fee is None or any(key not in object_storage for key in required):
                object_storage_cost = None
                missing.append("pricing.object_storage request or usage inputs")
            else:
                object_storage_cost = (
                    _number(object_storage["base_monthly_usd"], "pricing.object_storage.base_monthly_usd")
                    + max(0.0, object_stored
                          - _number(object_storage["included_gib"], "pricing.object_storage.included_gib"))
                    * _number(object_storage["extra_gib_month_usd"], "pricing.object_storage.extra_gib_month_usd")
                    + max(0.0, _number(object_storage["outbound_gib"], "pricing.object_storage.outbound_gib")
                          - _number(object_storage["included_outbound_gib"], "pricing.object_storage.included_outbound_gib"))
                    * _number(object_storage["extra_outbound_gib_usd"], "pricing.object_storage.extra_outbound_gib_usd")
                    + _number(request_fee, "pricing.object_storage.request_fee_monthly_usd")
                )
        shared = pricing.get("shared_infrastructure")
        shared_cost = None
        shared_categories = ({} if not isinstance(shared, dict) else
                             {key: value for key, value in shared.items() if key not in {"note", "source", "accessed"}})
        if shared_categories:
            shared_cost = _nullable_sum([_number(value, f"pricing.shared_infrastructure.{key}") if value is not None else None
                                         for key, value in shared_categories.items()])
        if not shared_categories:
            missing.append("pricing.shared_infrastructure")
        control_plane = pricing.get("kubernetes_control_plane_monthly_usd")
        if control_plane is None:
            control_plane_cost = None
            missing.append("pricing.kubernetes_control_plane_monthly_usd")
        else:
            control_plane_cost = _number(control_plane, "pricing.kubernetes_control_plane_monthly_usd")
        platform_subtotal = None if unsupported else _nullable_sum([node_cost, volume_cost, backup_cost, object_storage_cost, shared_cost, control_plane_cost])
        combined = _nullable_sum([platform_subtotal, model_cost])
        revenue = demand.get("revenue_monthly_usd")
        margin = None if revenue is None or _number(revenue, f"demand.{name}.revenue_monthly_usd") <= 0 or combined is None else _number(revenue, f"demand.{name}.revenue_monthly_usd") - combined
        margin_percent = None if margin is None else margin / _number(revenue, f"demand.{name}.revenue_monthly_usd") * 100
        platform_cost_per_success = None if platform_subtotal is None or fleet_terminals <= 0 else platform_subtotal / fleet_terminals
        combined_cost_per_success = None if combined is None or fleet_terminals <= 0 else combined / fleet_terminals
        results[name] = {"successful_workflows_per_month": workflows, "successful_agent_requests_per_month": agents,
                         "fleet_successful_workflows_per_month": fleet_workflows, "fleet_successful_agent_requests_per_month": fleet_agents,
                         "fleet_terminal_successes_per_month": fleet_terminals, "retention_demand_basis": demand_basis,
                         "peak_rate_per_s": peak_rate, "confirmed_rate_headroom_ceiling_per_s": ceiling,
                         "capacity_status": capacity_status, "tenant_placements": placements, "nodes": nodes,
                         "node_cost_usd": node_cost, "storage": {"database_logical_stock_gib": database_logical_stock,
                         "database_volume": {"provisioned_gib": provisioned, "minimum_gib": minimum, "volume_cost_usd": volume_cost,
                                             "billing_basis": "explicit provisioned database volume, never logical used bytes"},
                         "backup": {"retained_gib": backup_retained, "cost_usd": backup_cost,
                                    "billing_basis": "explicit retained backup GiB at the explicit backup unit price"},
                         "object_storage": {"stored_gib": object_stored, "cost_usd": object_storage_cost,
                                            "billing_basis": "object stock includes explicit retained versions and measured object growth"},
                         "basis": "database and object per-terminal growth are distinct; demand basis and retained stocks are explicit"},
                         "model_provider_cost_usd": model_cost, "shared_infrastructure_cost_usd": shared_cost,
                         "object_storage_cost_usd": object_storage_cost,
                         "kubernetes_control_plane_cost_usd": control_plane_cost,
                         "backup_cost_usd": backup_cost, "platform_subtotal_usd": platform_subtotal,
                         "combined_cost_usd": combined, "all_in_cost_status": "unsupported_pricing_inputs" if unsupported else "complete" if combined is not None else "incomplete",
                         "platform_cost_per_success_usd": platform_cost_per_success,
                         "combined_cost_per_success_usd": combined_cost_per_success,
                         "revenue_monthly_usd": revenue, "margin_usd": margin}
        results[name]["margin_percent"] = margin_percent
    return {"by_demand": results, "missing_inputs": sorted(set(missing)), "unsupported_pricing_keys": unsupported,
            "capacity_reasons": sorted(set(capacity_reasons)),
            "node_billing_basis": "Ready node time: monthly node price × node count × ready-node month fraction, never pod utilization"}


def build_card(scenario: dict[str, Any]) -> dict[str, Any]:
    evidence = _object(scenario.get("evidence"), "evidence")
    capacity, capacity_ref = (None, None)
    if evidence.get("capacity") is not None:
        capacity, capacity_ref = _load_reference(evidence["capacity"], "evidence.capacity")
    campaign, campaign_ref = _load_reference(evidence.get("campaign"), "evidence.campaign")
    soak_ref = None
    soak = None
    if evidence.get("soak") is not None:
        soak, soak_ref = _load_reference(evidence["soak"], "evidence.soak")
    matched, profile, steps = _matched_evidence(capacity, campaign, soak)
    tenant, pods, pvcs, storage_gib = _profile_occupancy(profile)
    postgres_pvc_gib = _postgres_pvc_gib(profile)
    soak_observation, generated = _soak_observation(soak)
    density = _density(scenario, tenant, pods, pvcs, storage_gib)
    costs = _costs(scenario, density, generated, postgres_pvc_gib, matched["confirmed_rate_per_s"])
    certification, reasons = _status(campaign, costs["capacity_reasons"])
    measurements = [{"index": index, "resource_usage": step.get("resource_usage"),
                     "latency": _object(step.get("verdict"), "campaign segment.verdict").get("latency"),
                     "execution_rates": step.get("execution_rates"), "storage_collector_frames": step.get("storage")}
                    for index, step in enumerate(steps)]
    return {"schema_version": 1, "kind": "hosting-capacity-card", "evidence": {"capacity": capacity_ref, "campaign": campaign_ref, "soak": soak_ref},
            "certification": {"status": certification, "reasons": reasons}, "frozen": matched,
            "resource_capacity": {"requested_quota": _object(profile["quota"], "capacity.profile.quota"),
                                  "max_component_requests": {"cpu_m": tenant.cpu_m, "memory_bytes": tenant.memory, "pods": pods, "pvcs": pvcs, "storage_gib": storage_gib},
                                  "actual_observed": {"by_segment": measurements,
                                                      "pooled_reliability": campaign.get("pooled_reliability"),
                                                      "soak_observation": soak_observation,
                                                      "recovery_limitations": None if soak is None else {
                                                          "capacity_evidence": soak.get("capacity_evidence"),
                                                          "object_storage": soak.get("object_storage"),
                                                      }}},
            "workload_rates": _observed_rates(campaign, steps), "density": density,
            "cost": costs,
            "limitations": ["Node allocatable resources are observed input, not advertised SKU capacity.",
                            "A zero density is infeasible and never produces a zero-cost hosting claim.",
                            "Retained-stock estimates scale two-hour synthetic-soak per-terminal growth by assumed demand and retention; idle growth, audit archives, result entropy, and reclamation can differ, so projections are illustrative until representative demand/retention is measured.",
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
