"""Run the production-shaped large-table regression with API memory sampling.

The test workload emits timestamped round summaries.  This driver samples the
long-lived API container throughout the run, aligns settled samples with each
round, and fails when settled RSS or cgroup working set shows material monotonic
growth after warmup.

Usage:
  python scripts/benchmark_large_table_responses.py --label no-request-trim
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import memory_sampler


_ROUND_PREFIX = "LARGE_TABLE_ROUND "
_TEST_PATH = "tests/e2e/platform/test_large_table_response_memory.py"
_MIB = 1024 * 1024


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label", required=True, help="Configuration label in the report"
    )
    parser.add_argument(
        "--trace-out",
        type=Path,
        default=Path("/tmp/bifrost/large-table-memory.csv"),
    )
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument(
        "--max-retained-mib",
        type=float,
        default=32.0,
        help="Maximum settled growth after the first two warmup rounds",
    )
    return parser.parse_args()


def _read_samples(path: Path) -> list[dict[str, float]]:
    samples: list[dict[str, float]] = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            try:
                parsed = {key: float(value) for key, value in row.items()}
            except (TypeError, ValueError):
                continue
            if parsed["rss_kb"] > 0:
                samples.append(parsed)
    return samples


def _nearest_sample(
    samples: list[dict[str, float]], timestamp: float
) -> dict[str, float]:
    sample = min(samples, key=lambda candidate: abs(candidate["ts_unix"] - timestamp))
    if abs(sample["ts_unix"] - timestamp) > 1.0:
        raise ValueError(f"no memory sample within 1s of settled marker {timestamp}")
    return sample


def _materially_monotonic(values: list[float], tolerance: float) -> bool:
    if len(values) < 3:
        return False
    return values[-1] - values[0] > tolerance and all(
        later >= earlier - tolerance / 4 for earlier, later in zip(values, values[1:])
    )


def _mib(value: float) -> float:
    return round(value / _MIB, 2)


def main() -> int:
    args = _parse_args()
    container = memory_sampler._worktree_container()
    # The sampler follows this stable name while test.sh boots its own stack.
    args.trace_out.parent.mkdir(parents=True, exist_ok=True)

    sampler = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("memory_sampler.py")),
            "--container",
            container,
            "--out",
            str(args.trace_out),
            "--interval",
            str(args.interval),
            "--maps-every",
            "10",
        ],
    )
    time.sleep(max(0.2, args.interval * 2))

    output_lines: list[str] = []
    try:
        test = subprocess.Popen(
            ["./test.sh", _TEST_PATH, "-s", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert test.stdout is not None
        for line in test.stdout:
            print(line, end="")
            output_lines.append(line)
        test_status = test.wait()
    finally:
        sampler.send_signal(signal.SIGINT)
        try:
            sampler.wait(timeout=10)
        except subprocess.TimeoutExpired:
            sampler.terminate()
            sampler.wait(timeout=5)

    rounds = []
    for line in output_lines:
        marker = line.find(_ROUND_PREFIX)
        if marker >= 0:
            rounds.append(json.loads(line[marker + len(_ROUND_PREFIX) :]))
    samples = _read_samples(args.trace_out)
    if len(rounds) < 3 or not samples:
        print(
            "benchmark did not produce enough round markers or memory samples",
            file=sys.stderr,
        )
        return 2

    try:
        settled = [_nearest_sample(samples, row["settled_at_unix"]) for row in rounds]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    workload_samples = [
        sample
        for sample in samples
        if rounds[0]["round_started_at_unix"]
        <= sample["ts_unix"]
        <= rounds[-1]["settled_at_unix"] + args.interval
    ]
    if not workload_samples:
        print("no memory samples overlap the measured workload", file=sys.stderr)
        return 2
    measured = settled[2:]
    max_retained_bytes = args.max_retained_mib * _MIB
    metrics = {
        "rss": [sample["rss_kb"] * 1024 for sample in measured],
        "cgroup_current": [sample["cgroup_bytes"] for sample in measured],
        "working_set": [sample["working_set_bytes"] for sample in measured],
        "inactive_anon": [sample["inactive_anon_bytes"] for sample in measured],
    }

    all_query_ms = [latency for row in rounds for latency in row["query_ms"]]
    report = {
        "label": args.label,
        "test_exit_code": test_status,
        "rounds": len(rounds),
        "response_bytes_per_round": rounds[-1]["response_bytes"],
        "request_latency_ms": {
            "min": round(min(all_query_ms), 2),
            "median": round(sorted(all_query_ms)[len(all_query_ms) // 2], 2),
            "max": round(max(all_query_ms), 2),
        },
        "health_max_ms": max(row["health_max_ms"] for row in rounds),
        "peak_rss_mib": round(
            max(sample["rss_kb"] for sample in workload_samples) / 1024, 2
        ),
        "peak_cgroup_current_mib": _mib(
            max(sample["cgroup_bytes"] for sample in workload_samples)
        ),
        "peak_working_set_mib": _mib(
            max(sample["working_set_bytes"] for sample in workload_samples)
        ),
        "settled_mib": {
            name: [_mib(value) for value in values] for name, values in metrics.items()
        },
        "retained_growth_mib": {
            name: _mib(values[-1] - values[0]) for name, values in metrics.items()
        },
        "materially_monotonic": {
            name: _materially_monotonic(values, max_retained_bytes)
            for name, values in metrics.items()
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))

    failed_metrics = [
        name
        for name in ("rss", "working_set")
        if metrics[name][-1] - metrics[name][0] > max_retained_bytes
        or report["materially_monotonic"][name]
    ]
    if failed_metrics:
        print(
            "settled memory regression in: " + ", ".join(failed_metrics),
            file=sys.stderr,
        )
        return 1
    return test_status


if __name__ == "__main__":
    sys.exit(main())
