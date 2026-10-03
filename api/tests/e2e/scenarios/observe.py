"""Turn the module's raw runs into per-cell observations.

A cell is one attempt by one run: ``"<run key>|<knob>|<target>"``. Write knobs
are observed from the tables themselves (read as admin), never from the
probe's own report: the value is the partition the document landed in, or
``None`` when it landed nowhere. Read knobs are observed as allowed/refused.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.scenarios.rule import (
    TARGETS,
    Child,
    Start,
    expected_allowed,
    expected_identity,
    expected_spawn,
)

WRITE_KNOBS = ("set_scope", "tables_scope", "raw_api", "context_override")
READ_KNOBS = ("config_scope", "integration_scope")
KNOBS = WRITE_KNOBS + READ_KNOBS


def cell_id(run_key: str, knob: str, target: str) -> str:
    return f"{run_key}|{knob}|{target}"


def landed(rows: dict[str, dict[str, Any]], doc_id: str) -> str | None:
    found = sorted(label for label, docs in rows.items() if doc_id in docs)
    return "+".join(found) if found else None


def run_cells(run: dict, rows: dict[str, dict[str, Any]], run_key: str) -> dict[str, Any]:
    """Every cell of one finished probe run."""
    execution = run["execution"]
    execution_id = execution["execution_id"]
    attempts = execution["result"]["attempts"]
    cells: dict[str, Any] = {}
    for target in TARGETS:
        for knob in WRITE_KNOBS:
            cells[cell_id(run_key, knob, target)] = landed(rows, f"{execution_id}-{knob}-{target}")
        for knob in READ_KNOBS:
            cells[cell_id(run_key, knob, target)] = bool(attempts[target][knob]["ok"])
    return cells


def reported_vs_landed(run: dict, rows: dict[str, dict[str, Any]]) -> list[str]:
    """Write attempts whose reported outcome disagrees with what landed."""
    execution = run["execution"]
    execution_id = execution["execution_id"]
    attempts = execution["result"]["attempts"]
    out = []
    for target in TARGETS:
        for knob in WRITE_KNOBS:
            reported = bool(attempts[target][knob]["ok"])
            where = landed(rows, f"{execution_id}-{knob}-{target}")
            if reported != (where is not None):
                out.append(f"{knob}|{target}: reported ok={reported}, landed={where}")
    return out


def identity_cells(run: dict, labels: dict[str, str], run_key: str) -> dict[str, Any]:
    identity = run["execution"]["result"]["identity"]
    user_id = identity["user_id"]
    return {
        cell_id(run_key, "identity", "user"): labels.get(user_id, user_id),
        cell_id(run_key, "identity", "is_platform_admin"): identity["is_platform_admin"],
    }


def observed_cells(
    start: Start, runs: dict[str, dict], rows: dict[str, dict[str, Any]], labels: dict[str, str]
) -> dict[str, Any]:
    """Every observed cell of one start's run tree."""
    cells: dict[str, Any] = {}

    def walk(run_key: str, children: tuple[Child, ...]) -> None:
        run = runs[run_key]
        cells.update(run_cells(run, rows, run_key))
        cells.update(identity_cells(run, labels, run_key))
        for child in children:
            child_key = f"{run_key}>{child.key}"
            spawned = "execution" in runs.get(child_key, {})
            cells[cell_id(child_key, "spawn", "-")] = spawned
            if spawned:
                walk(child_key, child.children)

    walk(start.key, start.children)
    return cells


def expected_cells(start: Start, observed: dict[str, Any]) -> dict[str, Any]:
    """The rule's value for every cell of the tree, plus children it allows.

    Cells of a child the rule refuses are expected only if the child ran, so
    their expectation is the rule applied to the original user's reach.
    """
    user = start.user
    cells: dict[str, Any] = {}

    def walk(run_key: str, run_as: str | None, children: tuple[Child, ...]) -> None:
        for target in TARGETS:
            allowed = expected_allowed(user, target)
            for knob in WRITE_KNOBS:
                cells[cell_id(run_key, knob, target)] = target if allowed else None
            for knob in READ_KNOBS:
                cells[cell_id(run_key, knob, target)] = allowed
        label, is_admin = expected_identity(user, run_as)
        cells[cell_id(run_key, "identity", "user")] = label
        cells[cell_id(run_key, "identity", "is_platform_admin")] = is_admin
        for child in children:
            child_key = f"{run_key}>{child.key}"
            cells[cell_id(child_key, "spawn", "-")] = expected_spawn(user, child)
            if observed.get(cell_id(child_key, "spawn", "-")):
                walk(child_key, child.run_as, child.children)

    walk(start.key, None, start.children)
    return cells
