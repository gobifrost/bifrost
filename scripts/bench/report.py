"""Render a benchmark ``result.json`` as a Markdown report."""

from __future__ import annotations


def _ms(dist: dict) -> str:
    return f"{dist['p50']:.0f} / {dist['p95']:.0f}"


def _classes_table(classes: list[dict]) -> list[str]:
    rows = [
        "| Class | Pass | Phase | OK / runs | Queue p50 / p95 ms | Run p50 / p95 ms | End-to-end p50 / p95 ms |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for entry in classes:
        for phase in ("sequential", "burst"):
            t = entry[phase]
            status = "" if t["failed"] == 0 else " ⚠"
            rows.append(
                f"| `{entry['name']}` | {entry['pass']} | {phase} | {t['succeeded']} / {t['runs']}{status} | "
                f"{_ms(t['queue_ms'])} | {_ms(t['run_ms'])} | {_ms(t['e2e_ms'])} |"
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
        f"- Classes: {profile['sequential']} sequential + burst of {profile['burst']} per class, two passes",
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
    ]
    failures = _failures(result["classes"])
    if failures:
        lines += ["## Failures", "", *failures, ""]
    return "\n".join(lines)
