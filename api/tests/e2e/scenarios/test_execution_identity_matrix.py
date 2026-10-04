"""Execution identity and scope changes, across every way a run starts.

Each test checks one start's cells against the managed-identity rule
(``rule.py``), except the cells listed in ``PRE_R3B`` (``pre_r3b.py``), which
record today's value until R3b implements the rule for them. The invariant
tests below check properties straight from the observations, so a mistake in
the rule cannot hide a mistake in the product.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from tests.e2e.scenarios import rule
from tests.e2e.scenarios.observe import (
    READ_KNOBS,
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
            observed.update(
                observed_cells(start, matrix["runs"], matrix["rows"], matrix["labels"])
            )
            expected.update(expected_cells(start, observed))
        return observed, expected
    if start.entry == "direct":
        assert start.person
        reads = matrix["direct"][start.key]
        observed = {
            cell_id(start.key, "direct_read", target): target
            in reads[target].get("partitions", [])
            for target in rule.TARGETS
        }
        expected = {
            cell_id(start.key, "direct_read", target): rule.expected_allowed(
                start.person, target
            )
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
        cell
        for cell in PRE_R3B
        if cell not in observed or PRE_R3B[cell] == expected.get(cell)
    ]
    assert not stale, "PRE_R3B entries to delete:\n" + "\n".join(stale)


def _tree_runs(start: rule.Start, runs: dict) -> list[tuple[str, dict]]:
    return [
        (key, run)
        for key, run in runs.items()
        if (key == start.key or key.startswith(f"{start.key}>")) and "execution" in run
    ]


def _write_cells(
    start: rule.Start, matrix: dict
) -> list[tuple[str, str, str, str | None]]:
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


def test_unattended_customer_workflow_stays_in_its_identitys_reach(matrix_runs: dict) -> None:
    """A Contoso workflow nobody called writes only in Contoso or Global."""
    reach = rule.REACH["mi:contoso"]
    violations = []
    for start in RUN_STARTS:
        if start.person is not None or start.probe != "contoso":
            continue
        for cell, _run_key, _target, where in _write_cells(start, matrix_runs):
            if where is not None and not set(where.split("+")) <= reach:
                violations.append(cell)
    assert not _unexplained(violations), "\n".join(_unexplained(violations))


def test_refused_writes_leave_no_row(matrix_runs: dict) -> None:
    """A write the run saw refused never exists; one it saw succeed always does."""
    problems = []
    for start in RUN_STARTS:
        for run_key, run in _tree_runs(start, matrix_runs["runs"]):
            problems += [
                f"{run_key}|{p}" for p in reported_vs_landed(run, matrix_runs["rows"])
            ]
    assert not problems, "\n".join(problems)


def test_children_never_exceed_original_reach(matrix_runs: dict) -> None:
    """Children write only inside the starter's reach; run_as never widens it."""
    violations = []
    for start in RUN_STARTS:
        reach = rule.REACH[start.user]
        for cell, run_key, _target, where in _write_cells(start, matrix_runs):
            if (
                ">" in run_key
                and where is not None
                and not set(where.split("+")) <= reach
            ):
                violations.append(cell)
    assert not _unexplained(violations), "\n".join(_unexplained(violations))


async def _lineage_rows(session_factory, execution_ids: list[str]) -> dict[str, tuple]:
    from sqlalchemy import select

    from src.core.database import close_db
    from src.models.orm.executions import Execution

    try:
        async with session_factory() as session:
            rows = await session.execute(
                select(
                    Execution.id,
                    Execution.run_user_id,
                    Execution.started_by_user_id,
                    Execution.root_execution_id,
                ).where(Execution.id.in_([UUID(e) for e in execution_ids]))
            )
            return {
                str(row[0]): tuple(str(v) if v else None for v in row[1:]) for row in rows
            }
    finally:
        await close_db()


def test_every_run_records_its_lineage(matrix_runs: dict, async_session_factory) -> None:
    """Every run in a start's tree runs for the start's user, rooted at its first run."""
    trees = {
        start: _tree_runs(start, matrix_runs["runs"]) for start in RUN_STARTS
    }
    ids = [
        run["execution"]["execution_id"] for runs in trees.values() for _, run in runs
    ]
    rows = asyncio.run(_lineage_rows(async_session_factory, ids))
    labels = matrix_runs["labels"]
    mismatches = []
    for start, runs in trees.items():
        expected = rule.expected_identity(start.user, None)[0]
        root = matrix_runs["runs"][start.key]["execution"]["execution_id"]
        for run_key, run in runs:
            run_user, started_by, run_root = rows[run["execution"]["execution_id"]]
            observed = (labels.get(run_user, run_user), labels.get(started_by, started_by), run_root)
            if observed != (expected, expected, root):
                mismatches.append(f"{run_key}: {observed} != {(expected, expected, root)}")
    assert not mismatches, "\n".join(mismatches)


async def _access_check_failures(session_factory, execution_ids: list[str]) -> set[tuple[str, str, Any]]:
    """(execution, resource type, target) of every report-only access check
    that the model would block."""
    from sqlalchemy import select

    from src.core.database import close_db
    from src.models.orm.audit import AuditLog

    try:
        async with session_factory() as session:
            rows = await session.execute(
                select(AuditLog.execution_id, AuditLog.resource_type, AuditLog.details["inputs"]["target"]).where(
                    AuditLog.action == "access.check",
                    AuditLog.outcome == "failure",
                    AuditLog.execution_id.in_([UUID(e) for e in execution_ids]),
                )
            )
            return {(str(execution), kind, target) for execution, kind, target in rows}
    finally:
        await close_db()


def test_access_checks_predict_the_rule(matrix_runs: dict, scenario_world: dict, async_session_factory) -> None:
    """Blocking exactly what the report-only checks flag yields the rule.

    (a) every check the model would block is a target the rule denies;
    (b) every attempt the server let through that the rule denies, and every
    child it started outside the reach, was flagged. Reach is per target, so
    one flag per (run, target) covers every knob.
    """
    label_of = {org_id: label for label, org_id in scenario_world["org_ids"].items()}

    def label(target: Any) -> str:
        return "global" if target is None else "*" if target == "*" else label_of.get(target, target)

    trees = {start: _tree_runs(start, matrix_runs["runs"]) for start in RUN_STARTS}
    ids = [run["execution"]["execution_id"] for runs in trees.values() for _, run in runs]
    flagged = {
        (execution, kind, label(target))
        for execution, kind, target in asyncio.run(_access_check_failures(async_session_factory, ids))
    }
    problems = []
    for start, runs in trees.items():
        user = start.user
        observed, _ = _start_cells(start, matrix_runs)
        for run_key, run in runs:
            execution = run["execution"]["execution_id"]
            for kind, target in sorted((k, t) for e, k, t in flagged if e == execution):
                allowed = rule.REACH[user] == rule.EVERYWHERE if target == "*" else rule.expected_allowed(user, target)
                if allowed:
                    problems.append(f"{run_key}: {kind} flagged at {target}, which the rule allows")
            for target in rule.TARGETS:
                if rule.expected_allowed(user, target):
                    continue
                let_through = any(
                    observed[cell_id(run_key, knob, target)] not in (None, False)
                    for knob in (*WRITE_KNOBS, *READ_KNOBS)
                )
                if let_through and (execution, "scope_switch", target) not in flagged:
                    problems.append(f"{run_key}: reached {target} unflagged")
            child_prefix = f"{run_key}>"
            for cell, spawned in observed.items():
                child_key, knob, _ = cell.split("|")
                if knob != "spawn" or not child_key.startswith(child_prefix) or ">" in child_key[len(child_prefix):]:
                    continue
                org = child_key.split("[", 1)[1].split(",", 1)[0]
                if spawned and org != "-" and not rule.expected_allowed(user, org):
                    if (execution, "child_run", org) not in flagged:
                        problems.append(f"{child_key}: started outside the reach, unflagged")
    assert not problems, "\n".join(problems)


# Functional journeys: these hold today and must hold unchanged after R3b.


def _journey(matrix: dict, key: str) -> dict:
    execution = matrix["journeys"][key]
    assert execution["status"] == "Success", f"{key}: {execution.get('error_message')}"
    return execution


def _row(matrix: dict, doc_id: str) -> tuple[str | None, str | None]:
    """(partition, created_by label) of a document, or (None, None)."""
    for label, docs in matrix["rows"].items():
        if doc_id in docs:
            return label, matrix["labels"].get(docs[doc_id], docs[doc_id])
    return None, None


def _onboarded(matrix: dict, key: str, person: str) -> None:
    run = _journey(matrix, key)
    assert run["result"]["outcome"] == "onboarded"
    assert _row(matrix, f"onboard-{run['execution_id']}") == (
        "contoso",
        f"person:{person}",
    )
    child = _journey(matrix, f"{key}>child")
    assert matrix["labels"].get(child["executed_by"]) == f"person:{person}"
    assert child["result"]["config"] == "contoso-value"
    assert child["result"]["entity_id"] == "contoso-tenant"
    assert child["result"]["secret_matches"] is True


def test_journey_customer_onboarding(matrix_runs: dict) -> None:
    """J1: an HR member onboards in their own org; the child reads its settings."""
    _onboarded(matrix_runs, "onboard_hr", "hr")
    denied = _journey(matrix_runs, "onboard_customer")
    assert denied["result"] == {"outcome": "not_permitted"}
    assert _row(matrix_runs, f"onboard-{denied['execution_id']}") == (None, None)


def test_journey_onboarding_other_customer_refused(matrix_runs: dict) -> None:
    """J2: the same onboarding aimed at another customer is refused and writes nothing."""
    run = _journey(matrix_runs, "onboard_other_org")
    assert run["result"] == {"outcome": "refused"}
    assert _row(matrix_runs, f"onboard-{run['execution_id']}") == (None, None)


def test_journey_staff_onboard_customer(matrix_runs: dict) -> None:
    """J3: provider staff onboard into a customer org named as input."""
    _onboarded(matrix_runs, "onboard_staff", "staff")


def test_journey_replay_as_submitter(matrix_runs: dict) -> None:
    """J4: an unattended provider dispatcher replays a stored request as its submitter."""
    _journey(matrix_runs, "dispatcher")
    child = _journey(matrix_runs, "dispatcher>child")
    assert matrix_runs["labels"].get(child["executed_by"]) == "person:customer"
    tag = matrix_runs["tag"]
    assert _row(matrix_runs, f"replay-request-{tag}") == ("contoso", "person:customer")


def test_journey_provider_fleet_job(matrix_runs: dict) -> None:
    """J5: an unattended provider job writes in every customer org."""
    run = _journey(matrix_runs, "fleet")
    for label in ("contoso", "fabrikam"):
        assert _row(matrix_runs, f"fleet-{run['execution_id']}-{label}")[0] == label


def test_journey_global_job_switches_to_provider(matrix_runs: dict) -> None:
    """J6: an unattended global job (platform org MI after migration) writes in the provider org."""
    run = _journey(matrix_runs, "global_switch")
    assert _row(matrix_runs, f"switch-{run['execution_id']}")[0] == "provider"


def test_journey_app_table_reads(scenario_world: dict) -> None:
    """J7: app-style reads: a customer sees their own org; staff and admin pick an org."""
    world = scenario_world

    def partitions(person: str, scope: str | None) -> list[str]:
        query = f"?scope={world['targets'][scope]}" if scope else ""
        resp = world["client"].post(
            f"/api/tables/{world['table']}/documents/query{query}",
            headers=world["people"][person].headers,
            json={"limit": 1000},
        )
        assert resp.status_code == 200, resp.text
        return sorted(
            d["id"].removeprefix("marker-")
            for d in resp.json()["documents"]
            if d["id"].startswith("marker-")
        )

    assert partitions("customer", None) == ["contoso"]
    assert partitions("staff", "contoso") == ["contoso"]
    assert partitions("admin", "fabrikam") == ["fabrikam"]


def test_journey_settings_fallback(matrix_runs: dict) -> None:
    """J8: an org without an override gets the global config, its own mapping and the secret."""
    run = _journey(matrix_runs, "reader_fabrikam")
    assert run["result"]["config"] == "global-default"
    assert run["result"]["entity_id"] == "fabrikam-tenant"
    assert run["result"]["secret_matches"] is True


def test_module_runtime(matrix_runs: dict, record_testsuite_property) -> None:
    """Record how long building the world and running every start took."""
    record_testsuite_property("scenario_runs_seconds", round(matrix_runs["elapsed"], 1))
