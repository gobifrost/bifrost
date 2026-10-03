"""Execution identity and scope changes, across every way a run starts.

Each test checks one start's cells against the managed-identity rule
(``rule.py``), except the cells listed in ``PRE_R3B`` (``pre_r3b.py``), which
record today's value until R3b implements the rule for them. The invariant
tests below check properties straight from the observations, so a mistake in
the rule cannot hide a mistake in the product.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.scenarios import rule
from tests.e2e.scenarios.observe import (
    WRITE_KNOBS,
    cell_id,
    expected_cells,
    observed_cells,
    reported_vs_landed,
)
from tests.e2e.scenarios.pre_r3b import PRE_R3B

RUN_STARTS = [s for s in rule.STARTS if s.entry not in ("org_override", "direct")]
OVERRIDE_STARTS = [s for s in rule.STARTS if s.entry == "org_override"]
DIRECT_STARTS = [s for s in rule.STARTS if s.entry == "direct"]

pytestmark = pytest.mark.e2e


def _mismatches(observed: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    out = []
    for cell in sorted(set(observed) | set(expected)):
        want = PRE_R3B.get(cell, expected.get(cell))
        got = observed.get(cell)
        if got != want:
            note = " (pre_r3b)" if cell in PRE_R3B else ""
            out.append(f"{cell}: observed {got!r}, expected {want!r}{note}")
    return out


def _start_cells(start: rule.Start, matrix: dict) -> tuple[dict, dict]:
    if start.entry == "org_override":
        cell = cell_id(start.key, "accepted", "-")
        observed = {cell: "refused_http" not in matrix["runs"][start.key]}
        assert start.person and start.request_org
        expected = {cell: rule.expected_allowed(start.person, start.request_org)}
        if observed[cell]:
            observed.update(observed_cells(start, matrix["runs"], matrix["rows"], matrix["labels"]))
            expected.update(expected_cells(start, observed))
        return observed, expected
    if start.entry == "direct":
        assert start.person
        reads = matrix["direct"][start.key]
        observed = {
            cell_id(start.key, "direct_read", target): target in reads[target].get("partitions", [])
            for target in rule.TARGETS
        }
        expected = {
            cell_id(start.key, "direct_read", target): rule.expected_allowed(start.person, target)
            for target in rule.TARGETS
        }
        return observed, expected
    observed = observed_cells(start, matrix["runs"], matrix["rows"], matrix["labels"])
    return observed, expected_cells(start, observed)


@pytest.mark.parametrize("start", rule.STARTS, ids=lambda s: s.key)
def test_cells_follow_rule(start: rule.Start, matrix_runs: dict) -> None:
    observed, expected = _start_cells(start, matrix_runs)
    problems = _mismatches(observed, expected)
    assert not problems, "\n".join(problems)


def test_pre_r3b_entries_are_live(matrix_runs: dict) -> None:
    """Every PRE_R3B entry names an observed cell that differs from the rule."""
    observed: dict[str, Any] = {}
    expected: dict[str, Any] = {}
    for start in rule.STARTS:
        o, e = _start_cells(start, matrix_runs)
        observed.update(o)
        expected.update(e)
    stale = [
        cell for cell in PRE_R3B
        if cell not in observed or PRE_R3B[cell] == expected.get(cell)
    ]
    assert not stale, "PRE_R3B entries to delete:\n" + "\n".join(stale)


def _tree_runs(start: rule.Start, runs: dict) -> list[tuple[str, dict]]:
    return [
        (key, run) for key, run in runs.items()
        if (key == start.key or key.startswith(f"{start.key}>")) and "execution" in run
    ]


def _write_cells(start: rule.Start, matrix: dict) -> list[tuple[str, str, str, str | None]]:
    """(cell, run key, target, partition landed) for every write in a start's tree."""
    observed, _ = _start_cells(start, matrix)
    out = []
    for run_key, _run in _tree_runs(start, matrix["runs"]):
        for target in rule.TARGETS:
            for knob in WRITE_KNOBS:
                cell = cell_id(run_key, knob, target)
                out.append((cell, run_key, target, observed[cell]))
    return out


def _unexplained(violations: list[str]) -> list[str]:
    return [v for v in violations if v not in PRE_R3B]


def test_customer_runs_stay_in_customer_reach(matrix_runs: dict) -> None:
    """No write from a run a customer started lands outside that customer's org."""
    violations = []
    for start in RUN_STARTS:
        if start.person is None or rule.REACH[start.person] == rule.EVERYWHERE:
            continue
        home = rule.REACH[start.person]
        for cell, _run_key, _target, where in _write_cells(start, matrix_runs):
            if where is not None and not set(where.split("+")) <= home:
                violations.append(cell)
    assert not _unexplained(violations), "\n".join(_unexplained(violations))


def test_unattended_customer_workflow_stays_home(matrix_runs: dict) -> None:
    """A Contoso workflow nobody called writes only in Contoso."""
    violations = []
    for start in RUN_STARTS:
        if start.person is not None or start.probe != "contoso":
            continue
        for cell, _run_key, _target, where in _write_cells(start, matrix_runs):
            if where is not None and where != "contoso":
                violations.append(cell)
    assert not _unexplained(violations), "\n".join(_unexplained(violations))


def test_refused_writes_leave_no_row(matrix_runs: dict) -> None:
    """A write the run saw refused never exists; one it saw succeed always does."""
    problems = []
    for start in RUN_STARTS:
        for run_key, run in _tree_runs(start, matrix_runs["runs"]):
            problems += [f"{run_key}|{p}" for p in reported_vs_landed(run, matrix_runs["rows"])]
    assert not problems, "\n".join(problems)


def test_children_never_exceed_original_reach(matrix_runs: dict) -> None:
    """Children write only inside the starter's reach; run_as never widens it."""
    violations = []
    for start in RUN_STARTS:
        reach = rule.REACH[start.user]
        for cell, run_key, _target, where in _write_cells(start, matrix_runs):
            if ">" in run_key and where is not None and not set(where.split("+")) <= reach:
                violations.append(cell)
    assert not _unexplained(violations), "\n".join(_unexplained(violations))
