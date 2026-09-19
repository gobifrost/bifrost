"""Synthetic evaluation runs through the durable agent engine.

``evaluation_synthetic`` is an explicit execution mode: synthetic runs use
the normal model loop, checkpointing, limits, output contracts, debugger
journal, child runs, and terminal events, but dispatch every non-engine
tool through the case simulator. Engine-owned primitives (``delegate_to_*``,
``delegate_agents``, ``sleep_until``) keep their durable semantics; children
of a synthetic parent stay synthetic.

Production guards:
- ``enqueue_agent_run`` rejects the reserved ``evaluation_synthetic``
  trigger type; only :func:`admit_synthetic_run` creates synthetic rows.
- Synthetic correlation never carries production event-trigger keys.
- Timer sleeps beyond the synthetic cap are rejected; admitted timers
  resolve deterministically without wall-clock waiting.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.services.agent_evaluations.quotas import MAX_SYNTHETIC_TIMER_SECONDS

EVALUATION_SYNTHETIC_MODE = "evaluation_synthetic"
PRODUCTION_MODE = "production"

ENGINE_OWNED_TOOL_PREFIX = "delegate_to_"
ENGINE_OWNED_TOOLS = frozenset({"delegate_agents", "sleep_until"})

SYNTHETIC_TIMER_MAX_SECONDS_DEFAULT = MAX_SYNTHETIC_TIMER_SECONDS

# Correlation keys that would tag a run as a production event trigger.
# Synthetic runs must never carry them.
PRODUCTION_TRIGGER_KEYS = frozenset(
    {"ticket_id", "production_trigger", "event_trigger", "appointment_id"}
)

SYNTHETIC_TIMER_FIRED_TEXT = "Synthetic timer fired (deterministic wake)."


class SyntheticRunnerError(Exception):
    """A synthetic run cannot be admitted, routed, or finished safely."""


def is_engine_tool(tool_name: str) -> bool:
    return (
        tool_name in ENGINE_OWNED_TOOLS
        or tool_name.startswith(ENGINE_OWNED_TOOL_PREFIX)
    )


def build_synthetic_correlation(
    *,
    suite_id: UUID,
    case_id: UUID,
    execution_id: UUID,
    side: str,
    candidate_id: UUID | None = None,
    repetition_index: int = 0,
) -> dict[str, Any]:
    """Bounded correlation locating a synthetic run in suite/case/candidate space."""
    if side not in ("baseline", "candidate"):
        raise SyntheticRunnerError(f"Unknown evaluation side {side!r}.")
    correlation: dict[str, Any] = {
        "evaluation_mode": EVALUATION_SYNTHETIC_MODE,
        "evaluation_suite_id": str(suite_id),
        "evaluation_case_id": str(case_id),
        "evaluation_execution_id": str(execution_id),
        "evaluation_side": side,
        "evaluation_repetition": repetition_index,
    }
    if candidate_id is not None:
        correlation["evaluation_candidate_id"] = str(candidate_id)
    return correlation


def is_synthetic_correlation(correlation: dict[str, Any] | None) -> bool:
    return bool(
        correlation and correlation.get("evaluation_mode") == EVALUATION_SYNTHETIC_MODE
    )


def assert_no_production_trigger(
    trigger_type: str | None, correlation: dict[str, Any] | None
) -> None:
    """Never tag an evaluation run as a production event trigger."""
    if trigger_type != EVALUATION_SYNTHETIC_MODE:
        raise SyntheticRunnerError(
            "Synthetic runs require trigger_type "
            f"{EVALUATION_SYNTHETIC_MODE!r}; got {trigger_type!r}."
        )
    forbidden = PRODUCTION_TRIGGER_KEYS & set((correlation or {}).keys())
    if forbidden:
        raise SyntheticRunnerError(
            "Synthetic correlation must not carry production trigger keys: "
            f"{', '.join(sorted(forbidden))}."
        )


def seed_side_fixtures(
    fixture: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Independent frozen fixture copies for the baseline and candidate sides."""
    from src.services.agent_evaluations.simulator_models import (
        canonical_hash,
        validate_fixture,
    )

    validate_fixture(fixture)
    baseline = copy.deepcopy(fixture)
    candidate = copy.deepcopy(fixture)
    assert canonical_hash(baseline) == canonical_hash(candidate)
    return baseline, candidate


def validate_synthetic_timer(
    arguments: dict[str, Any],
    *,
    max_seconds: int = SYNTHETIC_TIMER_MAX_SECONDS_DEFAULT,
    now: datetime | None = None,
) -> datetime:
    """Parse a synthetic timer request and enforce the deterministic cap."""
    from src.services.agent_runtime.timers import TimerError, parse_timer_args

    moment = now or datetime.now(timezone.utc)
    try:
        wake_at = parse_timer_args(arguments or {}, now=moment)
    except (TimerError, ValueError, TypeError, KeyError) as exc:
        raise SyntheticRunnerError(f"Invalid synthetic timer: {exc}") from exc
    delay = (wake_at - moment).total_seconds()
    if delay > max_seconds:
        raise SyntheticRunnerError(
            f"Synthetic timer requests {delay:.0f}s but the synthetic maximum "
            f"is {max_seconds}s."
        )
    return wake_at


class EngineToolSignal(Exception):
    """A tool call belongs to the engine (delegation/timer), not the simulator."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(tool_name)
        self.tool_name = tool_name


def synthetic_operation_id(run_id: UUID, provider_tool_call_id: str) -> str:
    """Scope provider tool-call idempotency to one root-or-child AgentRun."""
    return f"{run_id}:{provider_tool_call_id}"


class SimulatorToolRouter:
    """Route non-engine tool calls to one locked case simulator.

    ``real_executor`` is a proof hook: it must never be invoked. Tests pass
    a raising callable to prove zero real tool executors were called.
    """

    def __init__(
        self,
        simulator,
        *,
        correlation: dict[str, Any] | None = None,
        real_executor=None,
    ) -> None:
        self._simulator = simulator
        self._correlation = dict(correlation or {})
        self._real_executor = real_executor
        self.journal: list[dict[str, Any]] = []
        self.real_calls = 0
        self._sequence = 0

    @property
    def simulator(self):
        return self._simulator

    @property
    def correlation(self) -> dict[str, Any]:
        return dict(self._correlation)

    async def route(
        self, tool_name: str, arguments: dict[str, Any], tool_call_id: str
    ) -> Any:
        if is_engine_tool(tool_name):
            raise EngineToolSignal(tool_name)
        try:
            result = self._simulator.call(tool_name, dict(arguments or {}))
        except Exception as exc:
            self._journal(tool_name, arguments, tool_call_id, error=str(exc))
            raise
        self._journal(tool_name, arguments, tool_call_id, result=result)
        return _to_text(result)

    async def route_real(
        self, tool_name: str, arguments: dict[str, Any], tool_call_id: str
    ) -> Any:
        """Proof hook: any call here means the simulator was bypassed."""
        self.real_calls += 1
        if self._real_executor is not None:
            return await self._real_executor(tool_name, arguments, tool_call_id)
        raise SyntheticRunnerError(
            f"Real tool executor reached for {tool_name!r} in synthetic mode."
        )

    def _journal(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        from src.services.agent_evaluations.simulator_models import redact_value

        entry: dict[str, Any] = {
            "sequence": self._sequence,
            "kind": "tool_error" if error is not None else "tool_result",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": redact_value(dict(arguments or {})),
        }
        if error is not None:
            entry["error"] = error
        else:
            entry["result"] = redact_value(result)
        self.journal.append(entry)
        self._sequence += 1


class PersistentSimulatorToolRouter(SimulatorToolRouter):
    """Simulator router whose state and operation journal live in PostgreSQL.

    A model/tool retry presents the same ``tool_call_id``.  The operation row
    is therefore the idempotency boundary: it is read under the session row
    lock before a state transition and written in the same transaction as the
    new state.  Delegated synthetic children use the root run's session.
    """

    def __init__(
        self,
        session_factory,
        simulation_session_id: UUID,
        *,
        run_id: UUID,
        correlation,
    ):
        self._session_factory = session_factory
        self._simulation_session_id = simulation_session_id
        # Provider IDs are only idempotent within one model request. A child
        # may reuse the root's provider ID while sharing this simulator state.
        self._run_id = run_id
        super().__init__(None, correlation=correlation)

    async def validate_and_advance_timer(
        self, arguments: dict[str, Any], *, max_seconds: int, tool_call_id: str
    ) -> datetime:
        """Resolve one engine timer against the locked, replayable fixture clock."""
        from sqlalchemy import select

        from src.models.orm.agent_evaluations import AgentSimulationSession
        from src.services.agent_evaluations.quotas import MAX_SIM_RECORDS_PER_RUN

        operation_id = synthetic_operation_id(self._run_id, tool_call_id)
        async with self._session_factory() as db:
            simulation = await db.scalar(select(AgentSimulationSession).where(
                AgentSimulationSession.id == self._simulation_session_id,
            ).with_for_update())
            if simulation is None:
                raise SyntheticRunnerError("Synthetic simulation session disappeared.")
            state = copy.deepcopy(simulation.state or {})
            operations = state.setdefault("timer_operations", {})
            if operation_id in operations:
                return datetime.fromisoformat(operations[operation_id])
            if len(operations) >= MAX_SIM_RECORDS_PER_RUN:
                raise SyntheticRunnerError("Synthetic timer operation limit exceeded.")
            raw_clock = state.get("clock_time") or (simulation.fixture or {}).get(
                "seed_time", "2026-09-18T00:00:00+00:00"
            )
            try:
                current = datetime.fromisoformat(raw_clock)
                if current.tzinfo is None:
                    raise ValueError("timezone required")
            except (TypeError, ValueError) as exc:
                raise SyntheticRunnerError("Synthetic fixture clock is invalid.") from exc
            wake_at = validate_synthetic_timer(arguments, max_seconds=max_seconds, now=current)
            state["clock_time"] = wake_at.isoformat()
            state["clock_ticks"] = int(state.get("clock_ticks", 0)) + 1
            operations[operation_id] = wake_at.isoformat()
            simulation.state = state
            simulation.version += 1
            await db.commit()
            return wake_at

    async def route(
        self, tool_name: str, arguments: dict[str, Any], tool_call_id: str
    ) -> Any:
        if is_engine_tool(tool_name):
            raise EngineToolSignal(tool_name)
        from sqlalchemy import select

        from src.models.orm.agent_evaluations import (
            AgentSimulationSession,
            AgentSimulationToolRecord,
        )
        from src.services.agent_evaluations.simulator import Simulator, SyntheticToolError
        from src.services.agent_evaluations.simulator_models import redact_value

        args = dict(arguments or {})
        operation_id = synthetic_operation_id(self._run_id, tool_call_id)
        async with self._session_factory() as db:
            simulation = await db.scalar(
                select(AgentSimulationSession)
                .where(AgentSimulationSession.id == self._simulation_session_id)
                .with_for_update()
            )
            if simulation is None:
                raise SyntheticRunnerError("Synthetic simulation session disappeared.")
            prior = await db.scalar(
                select(AgentSimulationToolRecord).where(
                    AgentSimulationToolRecord.session_id == simulation.id,
                    AgentSimulationToolRecord.operation_id == operation_id,
                )
            )
            if prior is not None:
                if prior.error is not None:
                    self._journal(tool_name, args, tool_call_id, error=prior.error)
                    raise SyntheticToolError(prior.error, code="replayed_failure")
                self._journal(tool_name, args, tool_call_id, result=prior.result)
                return _to_text(prior.result)
            simulator = Simulator(dict(simulation.fixture or {}), dict(simulation.tool_schemas or {}))
            simulator._state = copy.deepcopy(simulation.state or {})
            sequence = (
                await db.scalar(
                    select(AgentSimulationToolRecord.sequence)
                    .where(AgentSimulationToolRecord.session_id == simulation.id)
                    .order_by(AgentSimulationToolRecord.sequence.desc())
                    .limit(1)
                )
            )
            simulator._sequence = 0 if sequence is None else sequence + 1
            # Check before entering the failure-record path: exceeding the
            # record limit must not itself append an unbounded failure record.
            simulator._check_record_capacity()
            try:
                result = simulator.call(tool_name, args)
            except SyntheticToolError as exc:
                simulation.state = copy.deepcopy(simulator.state)
                simulation.final_state_hash = simulator.state_hash()
                simulation.version += 1
                db.add(AgentSimulationToolRecord(
                    session_id=simulation.id,
                    sequence=simulator._sequence,
                    operation_id=operation_id,
                    tool_name=tool_name,
                    arguments=redact_value(args),
                    error=str(exc),
                    state_hash=simulator.state_hash(),
                ))
                await db.commit()
                self._journal(tool_name, args, tool_call_id, error=str(exc))
                raise
            record = simulator.records[-1]
            simulation.state = copy.deepcopy(simulator.state)
            simulation.final_state_hash = simulator.state_hash()
            simulation.version += 1
            db.add(AgentSimulationToolRecord(
                session_id=simulation.id,
                sequence=record["sequence"],
                operation_id=operation_id,
                tool_name=tool_name,
                arguments=record["arguments"],
                result=record["result"],
                state_hash=record["state_hash"],
            ))
            await db.commit()
        self._journal(tool_name, args, tool_call_id, result=result)
        return _to_text(result)


async def load_synthetic_router(
    session_factory, run_id: UUID
) -> PersistentSimulatorToolRouter | None:
    """Load the locked synthetic router for a root run or synthetic child.

    The consumer treats ``None`` as a fail-closed admission failure: it must
    not dispatch the model or a real tool.
    """
    from sqlalchemy import select

    from src.models.orm.agent_evaluations import AgentSimulationSession
    from src.models.orm.agent_runs import AgentRun
    from src.services.agent_runtime.execution_snapshot import is_synthetic_snapshot

    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.trigger_type != EVALUATION_SYNTHETIC_MODE:
            return None
        if not is_synthetic_snapshot(run.execution_snapshot):
            return None
        correlation = dict(run.correlation or {})
        if not is_synthetic_correlation(correlation):
            return None
        root_id = run.root_run_id or run.id
        # Older child admission paths can omit root_run_id; resolve parents
        # without ever accepting a differently marked ancestor.
        cursor = run
        visited = {run.id}
        while cursor.parent_run_id is not None:
            parent = await db.get(AgentRun, cursor.parent_run_id)
            if (
                parent is None
                or parent.id in visited
                or parent.trigger_type != EVALUATION_SYNTHETIC_MODE
                or not is_synthetic_snapshot(parent.execution_snapshot)
                or not is_synthetic_correlation(dict(parent.correlation or {}))
            ):
                return None
            visited.add(parent.id)
            root_id = parent.id
            cursor = parent
        simulation = await db.scalar(
            select(AgentSimulationSession).where(
                AgentSimulationSession.root_run_id == root_id
            )
        )
        if simulation is None:
            return None
        return PersistentSimulatorToolRouter(
            session_factory, simulation.id, run_id=run.id, correlation=correlation
        )


def _to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    import json

    return json.dumps(result, default=str, sort_keys=True)


def attach_synthetic(
    executor,
    router: SimulatorToolRouter,
    *,
    timer_max_seconds: int = SYNTHETIC_TIMER_MAX_SECONDS_DEFAULT,
):
    """Attach a simulator router to an engine executor (and its children)."""
    executor._synthetic_router = router
    executor._synthetic_timer_max_seconds = timer_max_seconds
    return executor


def collect_assertion_evidence(
    *,
    terminal_status: str,
    output: Any,
    malformed_output: bool = False,
    router: SimulatorToolRouter | None = None,
    simulator_state: dict[str, Any] | None = None,
    delegation_children: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project router journal + simulator state into assertion evidence."""
    tool_calls = []
    for entry in (router.journal if router is not None else []):
        if entry["kind"] in ("tool_result", "tool_error"):
            tool_calls.append(
                {
                    "name": entry["tool_name"],
                    "arguments": entry["arguments"],
                    "sequence": entry["sequence"],
                }
            )
    from src.services.agent_evaluations.simulator_models import canonical_hash

    resolved_state = simulator_state or {}
    return {
        "terminal_status": terminal_status,
        "output": output,
        "malformed_output": malformed_output,
        "tool_calls": tool_calls,
        "simulator_state": resolved_state,
        "simulator_state_hash": canonical_hash(resolved_state),
        "delegation": {"children": list(delegation_children or [])},
        "usage": usage or {},
        "real_tool_executions": router.real_calls if router is not None else 0,
        "output_schema": output_schema,
    }


async def admit_synthetic_run(
    session,
    *,
    candidate_snapshot: dict[str, Any],
    case_input: dict[str, Any] | None,
    output_schema: dict[str, Any] | None,
    correlation: dict[str, Any],
    org_id: UUID | None = None,
    agent_id: UUID | None = None,
    run_id: UUID | None = None,
    parent_run_id: UUID | None = None,
    root_run_id: UUID | None = None,
):
    """Admit one synthetic AgentRun row. The only writer of synthetic rows."""
    from src.models.orm.agent_runs import AgentRun
    from src.services.agent_runtime.execution_snapshot import (
        synthetic_snapshot_from_candidate,
    )
    from src.services.agent_evaluations.simulator_models import canonical_hash

    assert_no_production_trigger(EVALUATION_SYNTHETIC_MODE, correlation)
    snapshot = synthetic_snapshot_from_candidate(
        candidate_snapshot,
        case_input_hash=(
            canonical_hash(case_input or {}) if case_input is not None else None
        ),
    )
    resolved_id = run_id or uuid4()
    run = AgentRun(
        id=resolved_id,
        agent_id=agent_id,
        trigger_type=EVALUATION_SYNTHETIC_MODE,
        input=dict(case_input or {}),
        output_schema=output_schema,
        status="queued",
        org_id=org_id,
        correlation=correlation,
        execution_snapshot=snapshot,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id or resolved_id,
    )
    session.add(run)
    # Callers add simulation/result FKs without ORM relationships. Make the
    # parent row visible inside this transaction before those inserts flush.
    await session.flush()
    return run
