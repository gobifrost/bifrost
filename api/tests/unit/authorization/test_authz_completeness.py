"""Completeness: the minter's reads, AUTHZ_FACTS and the installed triggers agree.

A token is only as fresh as the epoch: every column the minter reads must be
either covered by an epoch trigger or read live on every request. Three
checks hold that equal (plan §9.2, D12):

- read side: the columns the minter actually reads equal the registry;
- registry and database: ``pg_trigger`` holds exactly the registry's
  ``authz_epoch_*`` triggers, with exactly its UPDATE columns;
- maintenance: a migration that disables triggers must bump the epoch after
  the change, in the same transaction.

Each check has a meta-test proving it fails when its invariant breaks.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import ColumnClause, Select, TableClause, event, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from sqlalchemy.sql import visitors

from src.core.database import get_engine
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.orm.forms import Form
from src.models.orm.users import User
from src.services.authorization import tokens
from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.services.authorization.authz import ANY_COLUMN, AUTHZ_FACTS
from src.services.authorization.tokens import RuntimeSubject, issue_access_token
from tests.unit.authorization.committed_runs import World, committed_world

_EPOCH_TABLE = "authz_versions"
_FACTS = {fact.table: fact for fact in AUTHZ_FACTS}
# A read of a table's rows, whatever columns: FROM, JOIN, count(*).
_ROWS = "<rows>"
# A statement whose reads cannot be resolved structurally.
_UNSUPPORTED = "?unsupported"


# --- Read side --------------------------------------------------------------


def _base_table(selectable: Any) -> TableClause | None:
    """The table behind an alias, or None for a subquery or other selectable."""
    while not isinstance(selectable, TableClause):
        selectable = getattr(selectable, "element", None)
        if selectable is None:
            return None
    return selectable


def _statement_reads(context: Any) -> set[tuple[str, str]]:
    """The (table, column) reads of one executed statement, from SQLAlchemy's
    compiled statement rather than its SQL text.

    For an ORM statement this is the core SELECT the ORM compiled (so
    ``load_only`` and joined eager loads are exact). A table appears as
    ``(table, _ROWS)``. A statement that is not a structured SELECT (textual
    SQL, driver-level SQL), or a column with no table, is reported as
    ``(_UNSUPPORTED, ...)`` so the check fails instead of reading as empty.
    """
    compiled = getattr(context, "compiled", None)
    state = getattr(compiled, "compile_state", None)
    statement = getattr(state, "statement", None) if state is not None else None
    if statement is None and compiled is not None:
        statement = compiled.statement
    if not isinstance(statement, Select):
        return {(_UNSUPPORTED, str(getattr(context, "statement", "")).strip()[:120])}
    reads: set[tuple[str, str]] = set()
    for element in visitors.iterate(statement, {"column_collections": False}):
        if isinstance(element, TableClause):
            reads.add((element.name, _ROWS))
        elif isinstance(element, ColumnClause):
            if element.table is None:
                reads.add((_UNSUPPORTED, f"column {element.name} has no table"))
                continue
            table = _base_table(element.table)
            if table is not None:
                reads.add((table.name, element.name))
    return reads


@asynccontextmanager
async def _capturing() -> AsyncIterator[set[tuple[str, str]]]:
    """Record the reads of every statement the application engine executes."""
    reads: set[tuple[str, str]] = set()

    def record(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        reads.update(_statement_reads(context))

    # Connect once first, so dialect initialization is not recorded.
    async with get_engine().connect() as warm:
        await warm.execute(text("SELECT 1"))
    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield reads
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _read_side_mismatches(reads: set[tuple[str, str]]) -> list[str]:
    problems: list[str] = []
    read_tables = {table for table, _ in reads}
    for table, column in sorted(reads):
        if table == _UNSUPPORTED:
            problems.append(f"unsupported statement: {column}")
            continue
        if table == _EPOCH_TABLE:
            continue
        fact = _FACTS.get(table)
        if fact is None:
            problems.append(f"{table}.{column} is read but no epoch trigger covers {table}")
        elif column == _ROWS:
            continue
        elif fact.update != ANY_COLUMN and column not in fact.update and column not in fact.live:
            problems.append(f"{table}.{column} is read but neither versioned nor read live")
    for fact in AUTHZ_FACTS:
        if fact.table not in read_tables:
            problems.append(f"{fact.table} is registered but the minter never reads it")
            continue
        declared = set(fact.live) | (set() if fact.update == ANY_COLUMN else set(fact.update))
        for column in sorted(declared - {column for table, column in reads if table == fact.table}):
            problems.append(f"{fact.table}.{column} is registered but the minter never reads it")
    return problems


async def _full_workflow(world: World) -> RuntimeSubject:
    """A separate actor, a restricted workflow outside any Solution, and
    impersonation: every execution, grant and people read."""
    contoso = await world.organization()
    fabrikam = await world.organization(provider=True)
    run_user = await world.person(contoso)
    actor = await world.person(fabrikam)
    role = await world.role("workflows.read")
    await world.assign(run_user, role.id, ("organization", contoso.id))
    workflow = await world.workflow(contoso, permission_mode="restricted", grants=("secrets.read",))
    execution = await world.execution(
        run_user=run_user, actor=actor, workflow=workflow, organization=contoso,
        impersonated_by_user_id=actor.id, impersonation_session_id=actor.id,
    )
    return RuntimeSubject("workflow_execution", execution.id)


async def _solution_workflow(world: World) -> RuntimeSubject:
    """A Solution workflow with a permission request: Solution state and consent reads."""
    organization = await world.organization()
    user = await world.person(organization)
    solution = await world.solution(organization)
    workflow = await world.workflow(organization, solution_id=solution.id)
    world.session.add(
        SolutionWorkflowPermissionRequest(
            solution_id=solution.id, workflow_id=workflow.id, requested_mode=WorkflowPermissionMode.RESTRICTED.value,
            requested_grants=[], request_digest="0" * 64, status="pending",
        )
    )
    await world.session.flush()
    execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
    return RuntimeSubject("workflow_execution", execution.id)


async def _service(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization)
    workflow = await world.workflow(organization)
    attempt = await world.service_attempt(run_user=user, workflow=workflow, organization=organization)
    return RuntimeSubject("service_attempt", attempt.id, attempt.service_id)


async def _agent_run(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization)
    solution = await world.solution(organization)
    agent = await world.agent(organization, solution=solution)
    run = await world.agent_run(run_user=user, actor=user, organization=organization, agent=agent)
    return RuntimeSubject("agent_run", run.id)


_SCENARIOS: tuple[Callable[[World], Awaitable[RuntimeSubject]], ...] = (
    _full_workflow,
    _solution_workflow,
    _service,
    _agent_run,
)


async def _capture_minting(engine: AsyncEngine, extra: Any = None) -> set[tuple[str, str]]:
    """The reads of minting a token for every scenario. ``extra`` is one more
    statement the minter runs inside its snapshot (a meta-test's injected read)."""
    async with committed_world(engine) as world:
        subjects = [await build(world) for build in _SCENARIOS]
        await world.session.commit()
        real_read_epoch = tokens.read_epoch

        async def read_epoch(db: AsyncSession) -> int:
            if extra is not None:
                await extra(db)
            return await real_read_epoch(db)

        tokens.read_epoch = read_epoch  # type: ignore[assignment]
        try:
            async with _capturing() as reads:
                for subject in subjects:
                    await issue_access_token(subject)
        finally:
            tokens.read_epoch = real_read_epoch  # type: ignore[assignment]
    return set(reads)


async def test_the_minters_reads_equal_the_registry(async_engine: AsyncEngine) -> None:
    assert _read_side_mismatches(await _capture_minting(async_engine)) == []


async def _textual(db: AsyncSession) -> None:
    await db.execute(text("SELECT last_login FROM users LIMIT 1"))


async def _driver_level(db: AsyncSession) -> None:
    await (await db.connection()).exec_driver_sql("SELECT count(*) FROM users")


async def _unversioned_column(db: AsyncSession) -> None:
    await db.execute(select(User.last_login).limit(1))


async def _unregistered_table(db: AsyncSession) -> None:
    await db.execute(select(func.count()).select_from(Form))


@pytest.mark.parametrize(
    ("extra", "problem"),
    [
        (_textual, "unsupported statement: SELECT last_login FROM users LIMIT 1"),
        (_driver_level, "unsupported statement: SELECT count(*) FROM users"),
        (_unversioned_column, "users.last_login is read but neither versioned nor read live"),
        (_unregistered_table, "forms.<rows> is read but no epoch trigger covers forms"),
    ],
    ids=["textual SQL", "driver-level SQL", "unversioned column", "unregistered table"],
)
async def test_an_injected_read_fails_the_check_through_real_capture(
    async_engine: AsyncEngine, extra: Callable[[AsyncSession], Awaitable[None]], problem: str
) -> None:
    assert problem in _read_side_mismatches(await _capture_minting(async_engine, extra))


def test_the_read_side_check_fails_for_an_unversioned_or_unregistered_read() -> None:
    covered = {
        (fact.table, column)
        for fact in AUTHZ_FACTS
        for column in ({"id"} if fact.update == ANY_COLUMN else set(fact.update)) | set(fact.live)
    }

    assert _read_side_mismatches(covered) == []
    assert _read_side_mismatches(covered | {("users", "last_login")}) == [
        "users.last_login is read but neither versioned nor read live"
    ]
    assert _read_side_mismatches(covered | {("forms", "name")}) == [
        "forms.name is read but no epoch trigger covers forms"
    ]


# --- Registry and database triggers ----------------------------------------

_ROW, _BEFORE, _INSERT, _DELETE, _UPDATE, _TRUNCATE = 1, 2, 4, 8, 16, 32
_ROW_FILTER_COLUMNS = re.compile(r"\b(?:old|new)\.(category|key|organization_id)\b")

_TRIGGERS = text(
    """
    SELECT c.relname, t.tgname, t.tgtype, t.tgconstraint <> 0, t.tgdeferrable, t.tginitdeferred,
           pg_get_triggerdef(t.oid)
      FROM pg_trigger t
      JOIN pg_class c ON c.oid = t.tgrelid
     WHERE NOT t.tgisinternal AND t.tgname LIKE 'authz\\_epoch\\_%'
    """
)


def _expected_triggers() -> dict[tuple[str, str], dict[str, Any]]:
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    deferred = {"row": True, "before": False, "constraint": True, "deferred": True}
    for fact in AUTHZ_FACTS:
        if fact.insert:
            expected[(fact.table, "authz_epoch_ins")] = {"event": _INSERT, **deferred}
        expected[(fact.table, "authz_epoch_upd")] = {"event": _UPDATE, **deferred}
        if fact.delete:
            expected[(fact.table, "authz_epoch_del")] = {"event": _DELETE, **deferred}
        expected[(fact.table, "authz_epoch_truncate")] = {
            "event": _TRUNCATE, "row": False, "before": False, "constraint": False, "deferred": False,
        }
    return expected


def _when_columns(definition: str) -> set[str]:
    when = definition.split(" WHEN ", 1)[1] if " WHEN " in definition else ""
    return set(re.findall(r"\bold\.(\w+|\*)", when))


async def _trigger_mismatches(connection: AsyncConnection) -> list[str]:
    actual = {
        (table, name): {
            "event": tgtype & (_INSERT | _DELETE | _UPDATE | _TRUNCATE),
            "row": bool(tgtype & _ROW),
            "before": bool(tgtype & _BEFORE),
            "constraint": constraint,
            "deferred": deferrable and initdeferred,
            "definition": definition,
        }
        for table, name, tgtype, constraint, deferrable, initdeferred, definition in (
            await connection.execute(_TRIGGERS)
        ).all()
    }
    expected = _expected_triggers()
    problems = [f"missing trigger {name} on {table}" for table, name in sorted(expected.keys() - actual.keys())]
    problems += [f"unregistered trigger {name} on {table}" for table, name in sorted(actual.keys() - expected.keys())]
    for key in sorted(expected.keys() & actual.keys()):
        found = actual[key]
        shape = {name: found[name] for name in ("event", "row", "before", "constraint", "deferred")}
        if shape != expected[key]:
            problems.append(f"{key[1]} on {key[0]} is {shape}, expected {expected[key]}")
        fact = _FACTS[key[0]]
        definition = found["definition"]
        if key[1] == "authz_epoch_upd":
            columns = {"*"} if fact.update == ANY_COLUMN else set(fact.update)
            if fact.row_filter is not None:
                columns |= set(_ROW_FILTER_COLUMNS.findall(definition))
            if _when_columns(definition) != columns:
                problems.append(f"authz_epoch_upd on {key[0]} compares {sorted(_when_columns(definition))}")
        elif key[1] in ("authz_epoch_ins", "authz_epoch_del") and (" WHEN " in definition) != (
            fact.row_filter is not None
        ):
            problems.append(f"{key[1]} on {key[0]} does not match its row filter")
    return problems


async def test_the_database_holds_exactly_the_registrys_triggers(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as connection:
        assert await _trigger_mismatches(connection) == []
    assert not any(
        fact.insert for fact in AUTHZ_FACTS if fact.table in {"executions", "agent_runs", "service_attempts"}
    )


@pytest.mark.parametrize(
    ("ddl", "problem"),
    [
        ("DROP TRIGGER authz_epoch_del ON roles", "missing trigger authz_epoch_del on roles"),
        (
            "CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON executions DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION authz_epoch_bump_trigger()",
            "unregistered trigger authz_epoch_ins on executions",
        ),
        (
            "DROP TRIGGER authz_epoch_upd ON users; CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON users "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (OLD.email IS DISTINCT FROM NEW.email) "
            "EXECUTE FUNCTION authz_epoch_bump_trigger()",
            "authz_epoch_upd on users compares ['email']",
        ),
    ],
    ids=["dropped trigger", "insert trigger on a run table", "narrowed update columns"],
)
async def test_the_trigger_check_fails_when_the_database_drifts(
    async_engine: AsyncEngine, ddl: str, problem: str
) -> None:
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            for statement in ddl.split("; "):
                await connection.execute(text(statement))
            assert problem in await _trigger_mismatches(connection)
        finally:
            await transaction.rollback()


# --- Maintenance -------------------------------------------------------------

_VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_DISABLES_TRIGGERS = re.compile(r"session_replication_role|DISABLE\s+TRIGGER", re.IGNORECASE)


def _maintenance_violations(sources: dict[str, str]) -> list[str]:
    """Migrations that disable triggers without bumping the epoch after the
    last disabling statement, in the migration's own transaction."""
    violations = []
    for name, source in sorted(sources.items()):
        disables = [match.end() for match in _DISABLES_TRIGGERS.finditer(source)]
        if not disables:
            continue
        if source.rfind("authz_epoch_bump_once()") < max(disables) or "autocommit_block" in source:
            violations.append(name)
    return violations


def test_migrations_that_disable_triggers_bump_the_epoch() -> None:
    sources = {path.name: path.read_text() for path in _VERSIONS.glob("*.py")}

    assert "20261030_authz_versions.py" in sources
    assert _maintenance_violations(sources) == []


def test_the_maintenance_check_requires_the_bump_after_the_change_in_the_same_transaction() -> None:
    assert _maintenance_violations(
        {
            "no_bump.py": 'op.execute("SET session_replication_role = replica")',
            "bump_first.py": 'op.execute("SELECT authz_epoch_bump_once()")\n'
            'op.execute("ALTER TABLE users DISABLE TRIGGER ALL")',
            "separate_transaction.py": 'op.execute("ALTER TABLE users DISABLE TRIGGER ALL")\n'
            "with op.get_context().autocommit_block():\n"
            '    op.execute("SELECT authz_epoch_bump_once()")',
            "same_transaction.py": 'op.execute("ALTER TABLE users DISABLE TRIGGER ALL")\n'
            'op.execute("UPDATE users SET name = name")\n'
            'op.execute("SELECT authz_epoch_bump_once()")',
            "plain.py": 'op.add_column("users", sa.Column("x", sa.Integer()))',
        }
    ) == ["bump_first.py", "no_bump.py", "separate_transaction.py"]
