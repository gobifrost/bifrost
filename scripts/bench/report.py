"""Render a benchmark ``result.json`` as a Markdown report."""

from __future__ import annotations

import json
from statistics import median

import jsoncodec


def _ms(dist: dict) -> str:
    return f"{dist['p50']:.0f} / {dist['p95']:.0f}"


def _cpu(entry: dict, phase: str) -> str:
    cpu = entry.get("cpu_ms_per_run")
    if phase != "sequential" or not cpu:
        return ""
    return f"{cpu.get('api', 0):.0f} / {cpu.get('worker', 0):.0f}"


def _db(entry: dict, phase: str) -> str:
    db = entry.get("db_per_run")
    if phase != "sequential" or not db:
        return " |  | "
    generated = db.get("wal_generated_kib")
    generated_cell = "" if generated is None else f"{generated:.1f}"
    return f"{db['xact']:.0f} | {db['wal_kib']:.1f} | {generated_cell}"


def _classes_table(classes: list[dict]) -> list[str]:
    rows = [
        "| Class | Pass | Phase | OK / runs | Queue p50 / p95 ms | Run p50 / p95 ms | End-to-end p50 / p95 ms "
        + "| CPU ms/run API / worker | DB transactions/run | WAL LSN advance KiB/run | Generated WAL KiB/run |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in classes:
        for phase in ("sequential", "burst"):
            t = entry[phase]
            status = "" if t["failed"] == 0 else " ⚠"
            rows.append(
                f"| `{entry['name']}` | {entry['pass']} | {phase} | {t['succeeded']} / {t['runs']}{status} | "
                f"{_ms(t['queue_ms'])} | {_ms(t['run_ms'])} | {_ms(t['e2e_ms'])} | {_cpu(entry, phase)} | {_db(entry, phase)} |"
            )
    return rows


def _import_costs(classes: list[dict]) -> list[str]:
    rows = [
        "## Import Diagnostic Counter Windows", "",
        "CPU and database counters include settling and background work. Startup work can affect the first window; these are not pure child costs.", "",
        "| Class | Pass | CPU ms/run API / worker | DB transactions/run | WAL LSN advance KiB/run | Generated WAL KiB/run |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for entry in classes:
        rows.append(f"| `{entry['name']}` | {entry['pass']} | {_cpu(entry, 'sequential')} | {_db(entry, 'sequential')} |")
    return [*rows, ""]


def _memory_table(snapshots: list[dict]) -> list[str]:
    rows = [
        "| After | API total MiB | API brk heap MiB | API anonymous (allocator arenas) MiB | API THP MiB | Worker working set MiB |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for snap in snapshots:
        api = snap["api"]
        workers = [c["working_set_mib"] for c in snap["containers"] if c["role"] == "worker"]
        worker = " + ".join(f"{w:.0f}" for w in workers) or "none"
        rows.append(
            f"| {snap['label']} | {api['rss_mib']:.0f} | {api['heap_rss_mib']:.0f} | "
            f"{api['anon_large_rss_mib']:.0f} | {api['thp_mib']:.0f} | {worker} |"
        )
    return rows


def _workers_table(classes: list[dict]) -> list[str]:
    measured = [entry for entry in classes if entry.get("worker_children")]
    if not measured:
        return []
    rows = [
        "## Worker processes",
        "",
        "Private (unshared) memory sampled twice a second on each worker pod while the class ran; runs shorter than that can be missed, so peaks are a lower bound. "
        + "Run children are processes forked for one run; templates are the processes they fork from.",
        "",
        "| Class | Pass | Peak run-child private MiB | Peak concurrent run children | Peak template private MiB |",
        "|---|---:|---:|---:|---:|",
    ]
    for entry in measured:
        w = entry["worker_children"]
        rows.append(
            f"| `{entry['name']}` | {entry['pass']} | {w['peak_child_private_mib']:.1f} | "
            f"{w['peak_children']} | {w['peak_template_private_mib']:.1f} |"
        )
    return [*rows, ""]


def _generated_snapshot_delta(before: dict, after: dict) -> tuple[float | None, str | None]:
    """A generated-WAL snapshot delta only when its reset epoch and bytes are valid."""
    resets = (before.get("wal_stats_reset"), after.get("wal_stats_reset"))
    if any(type(reset) is not str or not reset for reset in resets):
        return None, "missing or invalid `pg_stat_wal.stats_reset` marker"
    if resets[0] != resets[1]:
        return None, "`pg_stat_wal.stats_reset` changed"
    counters = (before.get("wal_generated_bytes"), after.get("wal_generated_bytes"))
    if any(type(counter) is not int or counter < 0 for counter in counters):
        return None, "missing or invalid generated WAL byte counter"
    if counters[1] < counters[0]:
        return None, "generated WAL byte counter regressed"
    return (counters[1] - counters[0]) / 1024 / 1024, None


def _database(result: dict) -> list[str]:
    idle = result.get("db_idle")
    if not idle:
        error = result.get("db_idle_error")
        if not error:
            return []
        return [
            "## Database", "",
            f"Database counter window invalid: {error.get('reason', 'unknown error')}. "
            "Raw before/after samples are retained in result.json; no rate or cost was calculated.", "",
        ]
    rate = idle["rate"]
    busiest = ", ".join(f"`{name}` {writes} / {scans}" for name, writes, scans in idle["top_tables"]) or "none"
    generated_rate = rate.get("wal_generated_kib_per_s")
    generated = "unavailable in this artifact" if generated_rate is None else f"{generated_rate:.1f} KiB generated WAL/s"
    lines = [
        "## Database", "",
        ("Idle counters are measured before load; the all-phases load window includes warmup, measurement and drain."
         if result["kind"] in {"capacity", "soak"} else
         "Per-run figures in the table above cover each class's sequential and burst runs, net of the idle rate measured at the end of the run."),
        "WAL LSN advance includes segment switches and padding. Generated WAL is the cluster-wide "
        "`pg_stat_wal.wal_bytes` counter. Neither measures WAL files retained on disk or archive storage.",
        "",
        f"- Idle: {rate['xact_per_s']:.1f} transactions/s, {rate['rows_written_per_s']:.1f} rows written/s, "
        f"{rate['wal_kib_per_s']:.1f} KiB WAL LSN advance/s, {generated} (over {idle['seconds']:.0f} s)",
        f"- Busiest tables while idle (rows written / scans): {busiest}",
    ]
    if "wal_stats_reset" in idle:
        lines.append(f"- Generated WAL stats-reset marker: `{idle['wal_stats_reset']}`")
    sized = [snap for snap in result["snapshots"] if "db_mib" in snap]
    if len(sized) >= 2:
        lines.append(
            f"- Database grew {sized[-1]['db_mib'] - sized[0]['db_mib']:.1f} MiB; "
            f"WAL LSN advance {sized[-1]['wal_mib'] - sized[0]['wal_mib']:.1f} MiB "
            f"between '{sized[0]['label']}' and '{sized[-1]['label']}'"
        )
        generated_delta_mib, generated_error = _generated_snapshot_delta(sized[0], sized[-1])
        if generated_error:
            lines.append(f"- Generated WAL counter unavailable across snapshots: {generated_error}")
        else:
            lines.append(f"- Generated WAL counter changed {generated_delta_mib:.1f} MiB over the same snapshots")
    return [*lines, ""]


def _imports_diagnostic(result: dict) -> list[str]:
    diagnostic = result.get("imports_diagnostic")
    if not diagnostic:
        return []
    rows = [
        "## Partial Imports Diagnostic", "",
        "This captures worker import traces after credential installation. The control is SDK/stdlib-only; the graph's installed `requests` import is an intentional third-party branch. It excludes browser work, complete integration latency, credential installation, resource baselines, consumer/fork/delivery, and log emission; it is not a Halo or customer-latency claim.",
        "Durable timestamps and engine durations below are execution records, not polling, waiting, browser, or customer-latency observations. The terminal-output read occurs after Success and is outside those spans. Trace child and phase walls are partial and overlapping, so they must not be summed. Raw API/worker pod/UID logs and worker process samples retain warm-versus-recycled-worker evidence.",
        "",
        f"Trace result: **{diagnostic.get('overall', 'unknown')}**.", "",
        "| Class | Pass | Trace result | Terminal output | Durable started→completed p50 / p95 ms | Engine p50 / p95 ms | Missing | Duplicate | Invalid metadata |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for group in diagnostic.get("groups", []):
        timing = group.get("durable_timing", {})
        durable = _partial_ms(timing.get("durable_started_to_completed_ms", {}))
        engine = _partial_ms(timing.get("engine_duration_ms", {}))
        output = group.get("execution_validation", {}).get("overall", "unavailable")
        rows.append(
            f"| `{group['class']}` | {group['pass']} | {group['overall']} | {output} | {durable} | {engine} | "
            f"{len(group.get('missing_execution_ids', []))} | {len(group.get('duplicate_execution_ids', []))} | "
            f"{len(group.get('invalid_events', []))} |"
        )
    return [*rows, ""]


def _partial_ms(summary: dict) -> str:
    """Format optional partial import-diagnostic timing without inventing zeroes."""
    if not isinstance(summary, dict) or not summary.get("count"):
        return "unavailable"
    p50, p95 = summary.get("p50_ms"), summary.get("p95_ms")
    if not isinstance(p50, (int, float)) or not isinstance(p95, (int, float)):
        return "unavailable"
    return f"{p50:.2f} / {p95:.2f}"


def _failures(classes: list[dict]) -> list[str]:
    lines: list[str] = []
    for entry in classes:
        for phase in ("sequential", "burst"):
            for failure in entry[phase]["failures"]:
                lines.append(f"- `{entry['name']}` pass {entry['pass']} {phase}: {failure['status']} — {failure.get('error') or ''}")
    return lines


def _header(result: dict) -> list[str]:
    profile = result["profile"]
    components = profile["components"]
    image_records = dict(result["images"])
    if result["kind"] == "ab":
        image_records.pop("api", None)
        image_records.update({f"API {name.upper()}": image for name, image in result["variants"].items()})
    images = ", ".join(
        f"{name} `{image['tag']}` ({image['id'][:19]})"
        + (f" source `{image['source_git_sha']}`" if image.get("source_git_sha") else "")
        for name, image in image_records.items()
    )
    deployed = result.get("deploy") or {}
    envelope = deployed.get("envelope", {})
    quota = profile["quota"]
    return [
        f"- Run: `{result['run_id']}`, started {result['started_at']}",
        f"- Harness: git `{result['git_sha']}`; runtime images {images}",
        f"- Cluster: Kind {result['kind_version']}, CloudNativePG {result['cnpg_version']}; host {result['host']['cpus']} CPUs, "
        f"load average at start {', '.join(f'{v:.2f}' for v in result['host']['load_average'])}; other Kind nodes on the host: "
        f"{', '.join(result['external_nodes']) or 'none'}",
        f"- Envelope: quota {quota['cpu']} CPU / {quota['memory']}; maximum occupancy {envelope.get('max_occupancy', '?')}, "
        f"headroom {envelope.get('headroom', '?')}; admitted at deploy {deployed.get('admitted', {}).get('admitted', '?')}",
        f"- Tenant: API {components['api']['replicas']} × ({components['api']['cpu']}, {components['api']['memory']}); "
        f"workers warm {profile['worker_policy']['warm_workers']} / max {profile['worker_policy']['max_workers']} × "
        f"({profile['worker_cpu']}, {profile['worker_policy']['worker_memory_mib']} MiB); Postgres {components['postgres']['cpu']}, "
        f"{components['postgres']['memory']}; requests == limits",
        f"- Allocator env: `{profile['env']}`; fake model latency {profile['fake_llm_latency_ms']} ms per call",
        f"- API pod replacements during run: {result['api_pod_changes'] if result.get('api_pod_changes') is not None else 'unmeasured'}",
    ]


def _capacity(result: dict) -> list[str]:
    diagnostic = result.get("not_capacity_certification") is True
    rows = [
        "## Short Diagnostic" if diagnostic else ("## Fixed-rate confirmation" if result.get("mode") == "confirmation-only" else "## Capacity search"),
        "",
        f"**Sustainable rate (confirmed): {result.get('sustainable_rate') or 'none confirmed'}**"
        + (" per second, within the strict drift tolerance and a 0.1 % per-class failure bound." if result.get("sustainable_rate") else "."),
        "",
        f"Mix `{result['mix_name']}`: `{result['profile']['capacity']['mixes'][result['mix_name']]}`; "
        f"gate thresholds {result['profile']['capacity']['thresholds_source']}. "
        + ("Confirmation only; no exploratory search or saturation bracket was measured."
         if result.get("mode") == "confirmation-only" else
         f"Exploratory ramp bracket (last pass, first not passing — failed or inconclusive; coarser gates than confirmation): {result.get('bracket')}"
         + (f"; stopped: {result['stopped']}" if result.get("stopped") else "")),
        "",
    ]
    if diagnostic:
        rows += ["This short run validates the workload and measurement pipeline. It does not certify sustainable capacity.", ""]
    preflight = result.get("preflight")
    if preflight:
        rows += [
            "", "## Representative Correctness Preflight", "",
            f"Result: **{preflight.get('overall', 'unknown')}**. This short prefix checks durable correctness only; it does not establish sustained capacity.",
            f"Submitted classes: `{preflight.get('submitted_classes', [])}`; required: `{preflight.get('required_classes', [])}`.",
        ]
        if preflight.get("overall") != "pass":
            rows.append("Raw sent/durable evidence and bounded API/worker log tails are retained with the result JSON; the long confirmation was not run.")
    rows += [
        "",
        "| Offered /s | Window s | Result | Completion/Arrival Ratio | Drift upper /s | Worst class e2e p95 s | Failures | Invalid / warnings |",
        "|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    for step in [*result.get("steps", []), *result.get("confirmations", [])]:
        verdict = step["verdict"]
        worst = max((gates["e2e_p95"]["value"] for gates in verdict["latency"].values()
                     if isinstance(gates["e2e_p95"]["value"], (int, float))), default=None)
        failures = sum(1 for record in step["records"] if record["outcome"] != "correct")
        notes = "; ".join(step["validity"]["invalid"] + step["validity"]["warnings"]) or ""
        throughput = verdict["throughput"]["value"]
        drift = verdict["drift"]["value"]
        kind = " (confirmation)" if step.get("confirm") else ""
        rows.append(
            f"| {step['rate']:.3f}{kind} | {step['window_s']:.0f} | {step['overall']} | "
            f"{'' if throughput is None else f'{throughput:.4f}'} | {'' if drift is None else f'{drift:.4f}'} | "
            f"{'' if worst is None else f'{worst:.2f}'} | {failures} | {notes} |"
        )
    rows += ["", "Completion/arrival ratio is a stability gate, not executions per second. It may exceed 1 when warm-up work completes in the measured window.", ""]
    steps = [*result.get("steps", []), *result.get("confirmations", [])]
    rows += _execution_rates(steps) + _resource_usage(steps) + _storage_frames(steps)
    return [*rows, "", *_baseline_memory(result), *_runtime_memory(result), *_database(result)]


def _execution_rates(steps: list[dict]) -> list[str]:
    rows: list[str] = []
    for index, step in enumerate(steps):
        measured = step.get("execution_rates")
        if measured is None:
            continue
        rows += [f"### Execution Rates — Step {index + 1}", "", measured["event_semantics"],
                 "Rates count top-level benchmark requests; nested agent tools are additional platform work.", "",
                 "| Class | Scheduled /s | Sent /s | HTTP Accepted /s | Durable Created /s | Terminal /s | Correct /s | Correct By Deadline /s |",
                 "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for name, data in [("Total", measured["total"]), *measured["by_class"].items()]:
            rates = data["event_rates_per_s"]
            cells = [rates[key] for key in ("scheduled", "sent", "http_accepted_responses", "durable_created",
                                            "durably_terminal", "correct_completed", "correct_by_deadline_completed")]
            rows.append(f"| {name} | " + " | ".join(f"{value:.4f}" for value in cells) + " |")
        rows += ["", measured["cohort_semantics"], "",
                 "| Class | Arrivals | Accepted Union | HTTP Only | Durable Only | Correct On Time | Correct Late | Failed Terminal | Unfinished | Duplicate IDs |",
                 "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for name, data in [("Total", measured["total"]), *measured["by_class"].items()]:
            cohort = data["arrival_cohort"]
            cells = [cohort[key] for key in ("scheduled", "accepted_union", "http_only", "durable_only",
                                             "correct_by_deadline", "correct_late", "failed_terminal", "unfinished_durable", "duplicate_request_ids")]
            rows.append(f"| {name} | " + " | ".join(str(value) for value in cells) + " |")
        rows += ["", "Latency follows the arrival cohort through drain, including late correct terminals. Missing timestamps are not zeroes.", "",
                 "| Class | Span | Observed / Missing | p50 / p95 / Max Seconds |", "|---|---|---:|---:|"]
        for name, data in [("Total", measured["total"]), *measured["by_class"].items()]:
            for span, dist in data.get("observed_latency_s", {}).items():
                values = " / ".join("unmeasured" if dist[key] is None else f"{dist[key]:.4f}" for key in ("p50", "p95", "max"))
                rows.append(f"| {name} | {span} | {dist['observed']} / {dist['missing']} | {values} |")
        activity = step.get("platform_activity")
        if activity:
            rows += ["", "Platform terminal counts include nested tools. Status counts do not establish expected-output correctness.", "",
                     "| Family | Type | Status | Terminal Count |", "|---|---|---|---:|"]
            for family in ("executions", "agent_runs"):
                for item in activity.get(family, []):
                    rows.append(f"| {family} | {item.get('type', '')} | {item['status']} | {item['count']} |")
            rows += ["", f"Execution log rows written in the measured window: {activity.get('execution_log_rows', 'unmeasured')}."]
    return [*rows, ""] if rows else []


def _storage_frames(steps: list[dict], periodic: list[dict] | None = None) -> list[str]:
    frames = []
    for index, step in enumerate(steps):
        for phase in ("before", "after"):
            frame = step.get("storage", {}).get(phase)
            if frame:
                frames.append((f"step {index + 1} {phase}", frame))
    frames.extend((sample.get("label", "periodic"), sample["storage"]) for sample in (periodic or []) if sample.get("storage"))
    if not frames:
        return []
    rows = ["## Storage Over Time", "",
            "Frames include warmup and drain. Database logical size, retained WAL, allocated data directories and object payloads overlap; do not add them. Allocated local bytes are not cloud provisioned or billable storage. Raw frames retain relation/index/TOAST sizes, tuple estimates, vacuum timestamps, connections/waits, Redis and Pooler counters, and declared backup retention.", "",
            "| Phase | Epoch Seconds | Database MiB | Retained WAL MiB | PostgreSQL Allocated MiB | Visible Object Payload MiB | Object Store Allocated MiB | Container Logs MiB | Redis Used MiB | Pooler Waiting / Max Wait Seconds |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    def mib(value: object) -> str:
        return "unmeasured" if value is None else f"{value / 2**20:.3f}"
    for label, frame in frames:
        pg, objects = frame.get("postgres", {}), frame.get("object_storage", {})
        database, pooler = pg.get("database", {}), frame.get("pgbouncer", {})
        logical = objects.get("logical_current_payload", {})
        waiting = (f"{pooler.get('pools', {}).get('cl_waiting', 'unmeasured')} / {pooler.get('max_wait_seconds', 'unmeasured')}"
                   if pooler.get("status") == "observed" else "unmeasured")
        values = [database.get("bytes"), database.get("wal_retained_bytes"), pg.get("physical_volume", {}).get("allocated_bytes"),
                  logical.get("total", {}).get("object_bytes"), objects.get("physical_volume", {}).get("allocated_bytes"),
                  frame.get("container_logs", {}).get("retained_bytes"), frame.get("redis", {}).get("memory", {}).get("used_memory")]
        rows.append(f"| {label} | {frame['collected_at']:.3f} | " + " | ".join(mib(value) for value in values) + f" | {waiting} |")
    return [*rows, ""]


def _retention(result: dict) -> list[str]:
    rows = ["## Storage Sizing and Retention Rehearsal", "",
            f"Stage: **{result.get('stage', 'partial')}**. This isolated fixture is storage evidence, not a capacity or hosting-price certification.", "",
            "Fixtures run in separate phases. Expected failures remain part of the retained history. Repetitive and seeded high-entropy payloads distinguish compression behavior; the fixture does not represent every customer workload.", ""]
    if result.get("error"):
        rows += [f"Invalid rehearsal: {result['error']}", ""]
    if result.get("not_exercised"):
        rows += [result["not_exercised"], ""]
    settings = result.get("canonical_run_retention_settings")
    if settings:
        rows += [f"Canonical retention settings: `{json.dumps(settings, sort_keys=True)}`.", ""]
    receipt = result.get("immutable_receipt")
    if receipt:
        rows += [f"Terminal records were retained before timestamp mutation: `{receipt['sha256']}` "
                 f"({len(receipt['execution_ids'])} explicit fixture execution IDs).", ""]
    sizes = result.get("before_backdate", {}).get("result_storage_by_case", {})
    if sizes:
        rows += ["| Fixture | Executions | Result Column Bytes | Payload UTF-8 Bytes |", "|---|---:|---:|---:|"]
        for name, size in sizes.items():
            rows.append(f"| {name} | {size['executions']} | {size['result_column_bytes']} | {size['payload_utf8_bytes']} |")
        rows += ["", "Result column size is distinct from total heap/index/TOAST and allocated disk bytes.", ""]
    frames = [{"label": "seeded", **result.get("before", {})}]
    frames += [{"label": phase["case"], "storage": phase.get("storage_after")} for phase in result.get("fixture_phases", [])]
    frames += [{"label": "before expiry", **result.get("before_retention", {})}, {"label": "after expiry", **result.get("after", {})}]
    rows += _storage_frames([], frames)
    for key in ("retention_dry_run", "retention_actual"):
        job = result.get(key, {}).get("job", {})
        if job:
            rows += [f"- {key}: PlatformJob `{job.get('id')}`, status `{job.get('status')}`."]
    rows += ["", result.get("method", {}).get("physical_limit", "DELETE and autovacuum need not shrink allocated PostgreSQL PVC bytes."),
             "Audit/artifact cleanup and backup/restore are not exercised by this rehearsal. Pricing remains unmeasured.", ""]
    return rows


def _recovery(result: dict) -> list[str]:
    found = result.get("result", {})
    rows = ["## Recovery Correctness", "",
            f"Drill: `{result.get('drill', 'unavailable')}`; status: **{result.get('status', 'partial')}**; stage: `{result.get('stage', 'initializing')}`.", "",
            f"Offered diagnostic rate: {result.get('rate')} /s. This correctness drill does not certify sustained capacity.", ""]
    if result.get("error"):
        rows += [f"Invalid measurement: {result['error']}", ""]
    proof = {key: found[key] for key in ("kind", "passed", "overall", "timing_checks", "assessment") if key in found}
    effects = found.get("effects", {})
    proof["effect_checks"] = {key: effects[key] for key in ("strict_effects", "terminal_and_loss") if key in effects}
    deadlines = found.get("deadlines", {})
    proof["deadline_checks"] = {key: deadlines[key] for key in ("normal_deadline", "recovery_deadline") if key in deadlines}
    injector = found.get("injector", {})
    proof["injector"] = {key: injector[key] for key in ("fault_at", "ready_at", "error", "injected_at") if key in injector}
    rows += ["```json", json.dumps(proof, indent=2, default=str), "```", "",
             "Raw accepted/unaccepted requests, durable records, append-only effects, exact fault target and lifecycle evidence are retained in result.json.", ""]
    for name, gap in result.get("d7_d9", {}).items():
        rows.append(f"- {name.upper()}: {gap}")
    cleanup = result.get("cleanup")
    if cleanup:
        rows += ["", f"Cleanup evidence: `{json.dumps(cleanup, sort_keys=True)}`."]
    return [*rows, ""]


def _baseline_memory(result: dict) -> list[str]:
    snapshots = [snap for snap in result.get("snapshots", []) if snap.get("containers")]
    if not snapshots:
        return []
    rows = ["## Idle and settled memory", "",
            "Per-container kubelet measurements; RSS and working set use different accounting from process PSS or private pages.", "",
            "| Phase | Pod | Working Set MiB | RSS MiB |", "|---|---|---:|---:|"]
    for snapshot in snapshots:
        for container in snapshot["containers"]:
            rows.append(f"| {snapshot['label']} | {container['pod']} | "
                        f"{container['working_set_mib']:.1f} | {container['rss_mib']:.1f} |")
    return [*rows, ""]


def _resource_usage(steps: list[dict]) -> list[str]:
    rows: list[str] = []
    for index, step in enumerate(steps):
        usage = step.get("resource_usage")
        if not usage:
            continue
        rows += [f"## Component Resources — Step {index + 1}", "", usage["limits"], "",
                 "CPU means cover observed intervals; coverage below 100% is missing evidence, not zero usage.", "",
                 "| Role | Mean CPU Cores | Peak Interval Cores | Peak Pod Throttle Ratio | Charged Peak MiB | Working Set Peak MiB | Observed Window |",
                 "|---|---:|---:|---:|---:|---:|---:|"]
        for role, data in sorted(usage["roles"].items()):
            charged = "unmeasured" if data["charged_peak_bytes"] is None else f"{data['charged_peak_bytes'] / 2**20:.1f}"
            working = "unmeasured" if data["working_set_peak_bytes"] is None else f"{data['working_set_peak_bytes'] / 2**20:.1f}"
            rows.append(f"| {role} | {data['mean_cpu_cores_observed']:.3f} | {data['max_interval_cpu_cores']:.3f} | "
                        f"{data['max_pod_throttled_ratio']:.3f} | {charged} | {working} | {data['observed_window_fraction']:.1%} |")
    return [*rows, ""] if rows else []


def _soak(result: dict) -> list[str]:
    rows = ["## Observational Soak", "",
            f"Status: **{result.get('status', 'partial')}**, stage `{result.get('stage', 'initializing')}`. "
            "This run observes sustained operation; it does not certify a new capacity rate.", "",
            f"Confirmed mixed offered rate: {result.get('confirmed_mixed_rate_per_s')} /s; "
            f"soak offered rate: {result.get('rate_per_s')} /s; measured duration: {result.get('measured_window_s')} seconds.", "",
            f"Source campaign: `{result.get('campaign_manifest_sha256', 'unavailable')}`.", ""]
    if result.get("error"):
        rows += [f"Invalid measurement: {result['error']}", ""]
    terminal = result.get("terminal_counts")
    if terminal:
        rows += [f"Durably terminal: {terminal['durably_terminal']}; correct successes: {terminal['correct_successes']}; "
                 f"unfinished durable records: {terminal['unfinished']}.", ""]
    growth = result.get("growth_per_1000_terminal")
    if growth:
        rows += ["### Growth Per 1,000 Durable Terminal Requests", "",
                 "Includes successful and failed terminals; signed changes preserve reclamation. "
                 "Database size, generated WAL and visible object payloads are distinct measurements.", "",
                 "| Counter | Signed Change |", "|---|---:|"]
        for name, value in growth.items():
            rows.append(f"| {name} | {'unmeasured' if value is None else f'{value:.1f}'} |")
    memory = result.get("host_memory_slopes", {})
    if memory:
        rows += ["", "### Memory Trends After 30 Minutes", "", memory["identity_limit"], "",
                 "Descriptive OLS slopes; role totals can rise when worker count changes. Inspect lifecycle and coverage.", "",
                 "| Role | Samples | Charged Memory Slope MiB/Hour | Coverage |", "|---|---:|---:|---|"]
        for role, evidence in sorted(memory.get("per_role", {}).items()):
            slope = evidence["slope_bytes_per_hour"]
            rows.append(f"| {role} | {evidence['sample_count']} | "
                        f"{'unmeasured' if slope is None else f'{slope / 2**20:.3f}'} | {evidence['coverage']} |")
    connections = result.get("connection_stability")
    if connections:
        rows += ["", "### Connection Sampling", "", connections["scope"], "",
                 f"Samples: {connections['samples']}; coverage: {connections['coverage']}. "
                 "Periodic storage frames additionally retain PostgreSQL waits, Redis and Pooler evidence.", ""]
    step = result.get("step")
    if step:
        rows += _execution_rates([step]) + _resource_usage([step])
        rows += _runtime_memory({"confirmations": [step]})
    rows += _storage_frames([step] if step else [], result.get("minute_metrics", []))
    return [*rows, *_baseline_memory(result), *_database(result)]


def _runtime_memory(result: dict) -> list[str]:
    """Simultaneous tenant totals, never the sum of independently timed peaks."""
    samples = []
    for step in [*result.get("steps", []), *result.get("confirmations", [])]:
        for interval in step.get("host", []):
            start, end = (step["started"] + value for value in step["window"])
            if not start <= interval["at"] <= end:
                continue
            tenant = [pod["memory_bytes"] for name, pod in interval["pods"].items()
                      if name.startswith("bench-tenant/") and "memory_bytes" in pod]
            if tenant:
                samples.append({key: sum(pod[key] for pod in tenant)
                                for key in ("current", "working_set", "anon", "file", "kernel")})
    if not samples:
        return []
    rows = ["## Observed runtime memory", "",
            "Simultaneous tenant totals sampled every five seconds during measured windows. "
            "Peaks are sampled lower bounds. Charged memory includes reclaimable cache; "
            "working set subtracts inactive file cache. File memory includes shared memory. "
            "Build jobs, object storage, collectors, fake providers and cluster infrastructure are outside these totals.", "",
            "| Metric | Sampled maximum MiB | Decimal GB |", "|---|---:|---:|"]
    for label, key in (("Charged", "current"), ("Working Set", "working_set"),
                       ("Anonymous", "anon"), ("File / Shared", "file"), ("Kernel", "kernel")):
        value = max(sample[key] for sample in samples)
        rows.append(f"| {label} | {value / 2**20:.1f} | {value / 1e9:.3f} |")
    return [*rows, ""]


def _fmt(value: object) -> str:
    if isinstance(value, dict) and "mean" in value:
        if value["low"] is None:
            return f"{value['mean']:.3g} (n={value['n']})"
        return f"{value['mean']:.3g} [{value['low']:.3g}, {value['high']:.3g}] (n={value['n']})"
    return "" if value is None else str(value)


def _ab(result: dict) -> list[str]:
    variants = result["variants"]
    rows = [
        "## A/B comparison",
        "",
        f"Mix `{result['mix_name']}` at {result['rate']}/s, order {result['order']}; each arm from a reset state. "
        f"A = `{variants['a']['tag']}` ({variants['a']['id'][:19]}), B = `{variants['b']['tag']}` ({variants['b']['id'][:19]}). "
        "Intervals are Student-t 95 %; a difference is claimed only when they do not overlap.",
        "",
        "| Metric | A | B | Differs |",
        "|---|---|---|---|",
    ]
    for row in result.get("comparison", []):
        if "infinite_in" in row:
            rows.append(f"| {row['metric']} | infinite in {row['infinite_in']['a']} runs | infinite in {row['infinite_in']['b']} runs | compare failures |")
            continue
        rows.append(f"| {row['metric']} | {_fmt(row.get('a'))} | {_fmt(row.get('b'))} | {'yes' if row.get('differs') else 'no'} |")
    return [*rows, ""]


def _elasticity(result: dict) -> list[str]:
    rows = [
        "## Worker Elasticity Observation", "",
        f"Status: **{result.get('status', 'in progress')}**. This is a first-request/burst diagnostic, not a sustained capacity certification.",
        "", "Each request uses the existing two-second wait workflow and validates its durable result. Node images remain cached.", "",
        "| Phase | Correct Within Deadline / Sent | End-to-End Min / Median / Max s | Late Completions | Phase Outcome |",
        "|---|---:|---:|---:|---|",
    ]
    for key in ("first_arrival", "burst"):
        wave = result.get(key)
        if not isinstance(wave, dict):
            continue
        requests = wave["requests"]
        correct = sum(row.get("correct") is True and row.get("deadline_met") is True
                      and row.get("durable_identity_matches") is True and row.get("error") is None
                      and row.get("duplicate_durable_rows") == 0 for row in requests)
        durations = [row["e2e_s"] for row in requests if row.get("timing_valid") is True]
        timing = " / ".join(f"{value:.6f}" for value in (min(durations), median(durations), max(durations))) if durations else "unmeasured"
        late = wave["late_count"] if wave.get("late_count") is not None else "unmeasured"
        rows.append(f"| {key} | {correct} / {len(requests)} | {timing} | {late} | {wave['verdict']} |")
    rows += ["", "## Policy and Resource Evidence", "",
             f"Effective policy before: `{result.get('policy_before', 'not observed')}`.",
             f"Effective policy after: `{result.get('policy_after', 'not observed')}`.", "",
             f"Lifecycle checkpoints: `{result.get('timeline', {}).get('path', 'not recorded')}`. "
             "DB, Pod and quota probes are sequential; heartbeat activity is not exact child concurrency.", ""]
    host = result.get("host_sampling", {})
    rows += [f"Host validity: `{host.get('validity', 'not observed')}`.", "",
             "| Resource Phase | Window s | Tenant CPU Seconds Observed | Tenant Memory Peak MiB Observed | Interval Coverage |",
             "|---|---:|---:|---:|---:|"]
    for name, phase in host.get("phases", {}).items():
        usage = phase["resource_usage"]
        tenant = usage["tenant"]
        memory = tenant["working_set_peak_bytes"]
        cpu = f"{tenant['cpu_seconds']:.3f}" if tenant["observed_seconds"] > 0 else "unmeasured"
        rows.append(f"| {name} | {usage['window_seconds']:.3f} | {cpu} | "
                    + (f"{memory / 2**20:.3f}" if memory is not None else "unmeasured")
                    + f" | {tenant['observed_window_fraction']:.3f} |")
    rows += ["", host.get("limits", "Resource evidence not yet collected."), "",
             f"Cleanup: `{result.get('cleanup', 'not yet observed')}`.", ""]
    return rows


def render(result: dict) -> str:
    if jsoncodec.NON_FINITE_NUMBERS in result:
        result = jsoncodec.decode(result)
    profile = result["profile"]
    lines = [
        f"# Benchmark: {result['profile_name']} {result['kind']} ({result['backend'] or 'no model'})",
        "",
        *_header(result),
        "",
    ]
    if result["kind"] == "capacity":
        return "\n".join([*lines, *_capacity(result)])
    if result["kind"] == "ab":
        return "\n".join([*lines, *_ab(result)])
    if result["kind"] == "drill":
        found = result.get("drill") or {}
        return "\n".join([*lines, "## Drill", "", f"API image `{result['api_image']}`.", "", "```json",
                          json.dumps({k: v for k, v in found.items() if k != "step"}, indent=2, default=str), "```", ""])
    if result["kind"] == "soak":
        return "\n".join([*lines, *_soak(result)])
    if result["kind"] == "retention-rehearsal":
        return "\n".join([*lines, *_retention(result)])
    if result["kind"] == "recovery-drill":
        return "\n".join([*lines, *_recovery(result)])
    if result["kind"] == "worker-elasticity":
        return "\n".join([*lines, *_elasticity(result)])
    import_diagnostic = bool(result.get("imports_diagnostic"))
    lines += [
        f"Classes: {profile['sequential']} sequential + {profile['burst_rounds']} bursts of {profile['burst']} per class, two passes.",
        "",
    ]
    if import_diagnostic:
        lines += [*_imports_diagnostic(result), *_import_costs(result["classes"])]
    else:
        lines += ["## Execution times", "", *_classes_table(result["classes"]), ""]
    if result.get("interactive_integration"):
        lines += ["## Synthetic Integration Validation", "",
                  "Matches the saved import graph's file count and byte budget; synthetic contents and provider delays do not reproduce Halo CPU or network behavior.", "",
                  f"Provider-span and terminal-output correlation: **{result['interactive_integration']['overall']}**. Raw per-request spans and validation failures are retained in result.json.", ""]
    lines += [
        "## Memory after each step",
        "",
        *_memory_table(result["snapshots"]),
        "",
        *_workers_table(result["classes"]),
        *_database(result),
    ]
    # Import diagnostics verify terminal output separately and retain durable
    # start/completion timing. Their absent created_at makes generic timing
    # statistics unavailable, not evidence that a successful execution failed.
    failures = [] if import_diagnostic else _failures(result["classes"])
    if failures:
        lines += ["## Failures", "", *failures, ""]
    return "\n".join(lines)
