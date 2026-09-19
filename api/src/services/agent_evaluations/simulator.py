"""Coherent stateful synthetic tool simulator.

Studio v1 never invokes real workflow tools. The simulator presents the
candidate with the real published tool schemas while routing every call to
deterministic, per-case locked fixture state:

- ``create_<entity>`` allocates a deterministic ID and inserts the row, so a
  later ``get_<entity>`` / ``list_<entities>`` / ``update_<entity>`` /
  ``delete_<entity>`` observes it;
- fixture ``rules`` match tool identity plus argument predicates to return
  data and apply state mutations without hardcoding product behavior;
- unhandled calls fail closed as visible test failures.

The dispatcher intentionally has no import path to the real integration,
workflow, MCP, or system-tool executors: tool names are allowlisted strings
resolved only against the frozen fixture and the snapshotted schemas. A
malicious fixture/tool name cannot escape to a live tool function.

The journal shape (sequence, tool, arguments, result/error, state hash)
matches production tool records so the model loop, contracts, delegation,
budgets, debugger, and scoring exercise the real runtime.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.services.agent_evaluations.simulator_models import (
    FixtureError,
    canonical_hash,
    fresh_state,
    redact_value,
    validate_fixture,
)


class SyntheticToolError(Exception):
    """Controlled synthetic tool failure (unhandled tool, bad args, no match)."""

    def __init__(self, message: str, *, code: str = "unhandled_tool") -> None:
        super().__init__(message)
        self.code = code


def _get_nested(arguments: dict[str, Any], path: str) -> Any:
    current: Any = arguments
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _rule_matches(rule: dict[str, Any], arguments: dict[str, Any]) -> bool:
    expected_args = rule.get("match_args", {})
    if not isinstance(expected_args, dict):
        return False
    for path, expected in expected_args.items():
        if _get_nested(arguments, path) != expected:
            return False
    return True


def _render_template(template: Any, arguments: dict[str, Any], state: dict) -> Any:
    """Render ``$args.<path>`` / ``$state.<path>`` references in a rule result."""
    if isinstance(template, str):
        if template.startswith("$args."):
            return _get_nested(arguments, template[len("$args."):])
        if template.startswith("$state."):
            return _get_nested(state, template[len("$state."):])
        return template
    if isinstance(template, dict):
        return {
            key: _render_template(item, arguments, state)
            for key, item in template.items()
        }
    if isinstance(template, list):
        return [_render_template(item, arguments, state) for item in template]
    return template


class Simulator:
    """One locked per-case simulation: state, clock, IDs, and call history."""

    def __init__(
        self,
        fixture: dict[str, Any],
        tool_schemas: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._fixture = validate_fixture(fixture)
        self._state = fresh_state(fixture)
        self._tool_schemas = dict(tool_schemas or {})
        self._records: list[dict[str, Any]] = []
        self._sequence = 0

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    def state_hash(self) -> str:
        return canonical_hash(self._state.get("entities", {}))

    def initial_state_hash(self) -> str:
        return canonical_hash(self._fixture.get("entities", {}))

    # -- dispatch ---------------------------------------------------------

    def allowed_tools(self) -> list[str]:
        allowed = self._fixture.get("allowed_tools") or []
        if allowed:
            return list(allowed)
        return sorted(self._tool_schemas.keys())

    def call(self, tool_name: str, arguments: dict[str, Any] | None) -> Any:
        """Execute one synthetic tool call against the locked case state."""
        self._check_record_capacity()
        args = dict(arguments or {})
        if tool_name not in self.allowed_tools():
            raise SyntheticToolError(
                f"Tool {tool_name!r} is not enabled for this simulation.",
                code="tool_not_allowed",
            )
        schema = self._tool_schemas.get(tool_name)
        if schema is not None:
            self._validate_against_schema(tool_name, schema, args)
        self._state["clock_time"] = self.now_iso()
        try:
            result = self._dispatch(tool_name, args)
        except SyntheticToolError:
            raise
        except Exception as exc:
            raise SyntheticToolError(str(exc), code="simulation_failed") from exc
        self._record(tool_name, args, result=result)
        return result

    def _validate_against_schema(
        self, tool_name: str, schema: dict[str, Any], args: dict[str, Any]
    ) -> None:
        from src.services.tool_schema import validate_arguments_against_schema

        issues, schema_error = validate_arguments_against_schema(schema, args)
        if schema_error:
            raise SyntheticToolError(
                f"Tool {tool_name!r} has an invalid snapshotted schema: {schema_error}",
                code="invalid_tool_schema",
            )
        if issues:
            raise SyntheticToolError(
                f"Arguments do not match the {tool_name!r} schema: {issues}",
                code="schema_mismatch",
            )

    def _dispatch(self, tool_name: str, args: dict[str, Any]) -> Any:
        for rule in self._fixture.get("rules", []):
            if rule.get("tool") == tool_name and _rule_matches(rule, args):
                return self._apply_rule(rule, args)
        handler = self._generic_handler(tool_name)
        if handler is not None:
            return handler(args)
        raise SyntheticToolError(
            f"Tool {tool_name!r} has no synthetic behavior for these arguments.",
            code="unhandled_tool",
        )

    # -- generic CRUD -----------------------------------------------------

    def _generic_handler(self, tool_name: str):
        for prefix in ("create_", "get_", "list_", "update_", "delete_"):
            if tool_name.startswith(prefix):
                raw = tool_name[len(prefix):]
                if prefix == "list_" and raw.endswith("s"):
                    entity = raw[:-1]
                else:
                    entity = raw
                return lambda args, p=prefix, e=entity: self._generic_crud(p, e, args)
        return None

    def _entities(self) -> dict[str, Any]:
        return self._state.setdefault("entities", {})

    def _generic_crud(
        self, prefix: str, entity: str, args: dict[str, Any]
    ) -> Any:
        entities = self._entities()
        collection = entities.setdefault(entity, {})
        if prefix == "create_":
            new_id = self._allocate_id(entity, args)
            row = {k: v for k, v in args.items() if k != "id"}
            row["id"] = new_id
            collection[new_id] = row
            return dict(row)
        if prefix == "get_":
            row_id = str(args.get("id", ""))
            row = collection.get(row_id)
            if row is None:
                raise SyntheticToolError(
                    f"{entity} {row_id!r} not found.", code="not_found"
                )
            return dict(row)
        if prefix == "list_":
            rows = list(collection.values())
            filters = {
                k: v for k, v in args.items() if k not in ("limit", "offset")
            }
            if filters:
                rows = [
                    row
                    for row in rows
                    if all(row.get(k) == v for k, v in filters.items())
                ]
            return {"items": [dict(row) for row in rows], "total": len(rows)}
        if prefix == "update_":
            row_id = str(args.get("id", ""))
            row = collection.get(row_id)
            if row is None:
                raise SyntheticToolError(
                    f"{entity} {row_id!r} not found.", code="not_found"
                )
            for key, value in args.items():
                if key != "id":
                    row[key] = value
            return dict(row)
        if prefix == "delete_":
            row_id = str(args.get("id", ""))
            if row_id not in collection:
                raise SyntheticToolError(
                    f"{entity} {row_id!r} not found.", code="not_found"
                )
            del collection[row_id]
            return {"id": row_id, "deleted": True}
        raise SyntheticToolError(
            f"Tool {prefix + entity!r} is not a known synthetic operation.",
            code="unhandled_tool",
        )

    def _allocate_id(self, entity: str, args: dict[str, Any]) -> str:
        if args.get("id") is not None:
            return str(args["id"])
        counters = self._state.setdefault("id_counters", {})
        nxt = int(counters.get(entity, 0)) + 1
        counters[entity] = nxt
        prefix = self._state.get("id_prefix", entity)
        return f"{prefix}-{nxt:04d}"

    def now_iso(self) -> str:
        """Advance the persisted clock, including prior engine timer advances."""
        base = self._state.get("clock_time") or self._fixture.get(
            "seed_time", "2026-09-18T00:00:00+00:00"
        )
        tick = int(self._state.get("clock_ticks", 0)) + 1
        self._state["clock_ticks"] = tick
        current = (datetime.fromisoformat(base) + timedelta(seconds=1)).isoformat()
        self._state["clock_time"] = current
        return current

    # -- fixture rules ----------------------------------------------------

    def _apply_rule(self, rule: dict[str, Any], args: dict[str, Any]) -> Any:
        for mutation in rule.get("mutate", []):
            self._apply_mutation(mutation, args)
        return _render_template(rule.get("return", {"ok": True}), args, self._state)

    def _apply_mutation(self, mutation: dict[str, Any], args: dict[str, Any]) -> None:
        op = mutation.get("op")
        entities = self._entities()
        if op == "upsert":
            collection = entities.setdefault(mutation["entity"], {})
            row_id = str(_render_template(mutation["id"], args, self._state))
            row = collection.setdefault(row_id, {"id": row_id})
            for key, template in (mutation.get("set") or {}).items():
                row[key] = _render_template(template, args, self._state)
        elif op == "delete":
            collection = entities.setdefault(mutation["entity"], {})
            row_id = str(_render_template(mutation["id"], args, self._state))
            collection.pop(row_id, None)
        else:
            raise FixtureError(f"Unknown fixture mutation {op!r}.")

    # -- journal ----------------------------------------------------------

    def _record(
        self, tool_name: str, args: dict[str, Any], *, result: Any
    ) -> None:
        self._records.append(
            {
                "sequence": self._sequence,
                "tool": tool_name,
                "arguments": redact_value(args),
                "result": redact_value(result),
                "state_hash": self.state_hash(),
            }
        )
        self._sequence += 1

    def record_failure(self, tool_name: str, args: dict[str, Any], error: str) -> None:
        self._check_record_capacity()
        self._records.append(
            {
                "sequence": self._sequence,
                "tool": tool_name,
                "arguments": redact_value(args),
                "error": error,
                "state_hash": self.state_hash(),
            }
        )
        self._sequence += 1

    def _check_record_capacity(self) -> None:
        from src.services.agent_evaluations.quotas import MAX_SIM_RECORDS_PER_RUN

        # Persistent routers restore sequence from PostgreSQL, while their
        # in-memory record list starts empty. Count the entire session history.
        if self._sequence >= MAX_SIM_RECORDS_PER_RUN:
            raise SyntheticToolError(
                f"Simulation exceeded {MAX_SIM_RECORDS_PER_RUN} tool records.",
                code="quota_exceeded",
            )
