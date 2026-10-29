"""Render a benchmark ``result.json`` as a Markdown report."""

from __future__ import annotations

import json


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
        return " | "
    return f"{db['xact']:.0f} | {db['wal_kib']:.1f}"


def _classes_table(classes: list[dict]) -> list[str]:
    rows = [
        "| Class | Pass | Phase | OK / runs | Queue p50 / p95 ms | Run p50 / p95 ms | End-to-end p50 / p95 ms "
        + "| CPU ms/run API / worker | DB transactions/run | WAL KiB/run |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
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


def _database(result: dict) -> list[str]:
    idle = result.get("db_idle")
    if not idle:
        return []
    rate = idle["rate"]
    busiest = ", ".join(f"`{name}` {writes} / {scans}" for name, writes, scans in idle["top_tables"]) or "none"
    lines = [
        "## Database",
        "",
        "Per-run figures in the table above cover each class's sequential and burst runs, net of the idle rate measured at the end of the run.",
        "",
        f"- Idle: {rate['xact_per_s']:.1f} transactions/s, {rate['rows_written_per_s']:.1f} rows written/s, "
        f"{rate['wal_kib_per_s']:.1f} KiB WAL/s (over {idle['seconds']:.0f} s)",
        f"- Busiest tables while idle (rows written / scans): {busiest}",
    ]
    sized = [snap for snap in result["snapshots"] if "db_mib" in snap]
    if len(sized) >= 2:
        lines.append(
            f"- Database grew {sized[-1]['db_mib'] - sized[0]['db_mib']:.1f} MiB; "
            f"WAL written {sized[-1]['wal_mib'] - sized[0]['wal_mib']:.1f} MiB "
            f"between '{sized[0]['label']}' and '{sized[-1]['label']}'"
        )
    return [*lines, ""]


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
    images = ", ".join(f"{name} `{image['tag']}` ({image['id'][:19]})" for name, image in result["images"].items())
    deployed = result.get("deploy") or {}
    envelope = deployed.get("envelope", {})
    quota = profile["quota"]
    return [
        f"- Run: `{result['run_id']}`, started {result['started_at']}",
        f"- Build: git `{result['git_sha']}`; images {images}",
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
        f"- API pod replacements during run: {result['api_pod_changes']}",
    ]


def _capacity(result: dict) -> list[str]:
    rows = [
        "## Capacity search",
        "",
        f"**Sustainable rate (confirmed): {result.get('sustainable_rate') or 'none confirmed'}**"
        + (" per second, within the strict drift tolerance and a 0.1 % per-class failure bound." if result.get("sustainable_rate") else "."),
        "",
        f"Mix `{result['mix_name']}`: `{result['profile']['capacity']['mixes'][result['mix_name']]}`; "
        f"gate thresholds {result['profile']['capacity']['thresholds_source']}. "
        f"Exploratory ramp bracket (last pass, first fail; coarser gates than confirmation): {result.get('bracket')}" + (f"; stopped: {result['stopped']}" if result.get("stopped") else ""),
        "",
        "| Offered /s | Window s | Result | Throughput | Drift upper /s | Worst class e2e p95 s | Failures | Invalid / warnings |",
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


def render(result: dict) -> str:
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
    lines += [
        f"Classes: {profile['sequential']} sequential + {profile['burst_rounds']} bursts of {profile['burst']} per class, two passes.",
        "",
        "## Execution times",
        "",
        *_classes_table(result["classes"]),
        "",
        "## Memory after each step",
        "",
        *_memory_table(result["snapshots"]),
        "",
        *_workers_table(result["classes"]),
        *_database(result),
    ]
    failures = _failures(result["classes"])
    if failures:
        lines += ["## Failures", "", *failures, ""]
    return "\n".join(lines)
