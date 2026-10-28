"""Render a benchmark ``result.json`` as a Markdown report."""

from __future__ import annotations


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
        "| After | API total MiB | API heap MiB | API arenas/large MiB | API THP MiB | Worker working set MiB |",
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


def render(result: dict) -> str:
    profile = result["profile"]
    lines = [
        f"# Benchmark: {result['profile_name']} ({result['backend'] or 'no model'})",
        "",
        f"- Run: `{result['run_id']}`, started {result['started_at']}",
        f"- Build: git `{result['git_sha']}`, image `{result['image_id']}`",
        f"- Topology: 1 API replica, {profile['worker_replicas']} worker replica(s), no scheduler",
        f"- Allocator env: `{profile['env']}`; THP emulation: {profile['thp_emulation']}",
        f"- Classes: {profile['sequential']} sequential + {profile['burst_rounds']} bursts of {profile['burst']} per class, two passes",
        f"- API pod restarts during run: {result['api_pod_changes']}",
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
