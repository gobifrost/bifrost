"""Judge a stored ``access.check`` event again, and answer what-if questions.

A stored event keeps the inputs of its decision (``details.inputs``), the run
user, the workflow and the note kind. "Now" rebuilds the note from them and
passes it through the writer's ``judge`` against the run user's current
context and the workflow's current powers, so then and now go through one
function. Facts that were themselves decisions (an entry's ``allowed``, a
file policy's ``model``) are recomputed first. Decisions that depend on data
the event does not keep are reported as unavailable instead of guessed.
"""

from __future__ import annotations

from functools import cache
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import ALL_ORGS, Note, NoteKind, NoteTarget
from src.models.contracts.access_checks import NowUnavailable
from src.models.contracts.access_list import AccessEntry
from src.models.orm.audit import AuditLog
from src.models.orm.workflows import Workflow
from src.repositories.agents import AgentRepository
from src.repositories.org_scoped import OrgScopedRepository
from src.repositories.workflows import WorkflowRepository
from src.services.access_check_entry import run_user_may_open
from src.services.access_check_policies import load_policy_principal
from src.services.access_check_writer import judge
from src.services.authorization.enforce import operation_key
from src.services.authorization.explain import Trace, check_operation, load_powers, load_run_user
from src.services.file_policy_service import FilePolicyService

_ENTRY_REPOSITORIES: dict[str, type[OrgScopedRepository[Any]]] = {
    "workflow": WorkflowRepository,
    "agent": AgentRepository,
}


@cache
def _entries_by_key() -> dict[str, AccessEntry]:
    from src.services.access_list import ACCESS_LIST

    index: dict[str, AccessEntry] = {}
    for entry in ACCESS_LIST:
        if entry.mcp_tool is not None:
            continue
        index[operation_key(entry)] = entry
        index[f"{entry.method} {entry.path}"] = entry
    return index


def entry_for_key(operation: str) -> AccessEntry | None:
    """The access-list entry a catalog id or a stored "METHOD /path" names."""
    return _entries_by_key().get(operation)


def _uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def stored_note(row: AuditLog) -> Note:
    """The note an ``access.check`` event was written from."""
    details = cast("dict[str, Any]", row.details)
    facts = dict(details["inputs"])
    facts.pop("operation")
    stored_target = facts.pop("target")
    target: NoteTarget = ALL_ORGS if stored_target == ALL_ORGS else _uuid(stored_target)
    return Note(cast(NoteKind, row.resource_type), target, facts)


async def rerun(db: AsyncSession, row: AuditLog) -> tuple[Trace | None, NowUnavailable | None]:
    """The stored event judged now, or why it cannot be."""
    if row.user_id is None:
        return None, "run_user_missing"
    run_user = await load_run_user(db, row.user_id)
    if run_user is None:
        return None, "run_user_missing"
    details = cast("dict[str, Any]", row.details)
    workflow_id = _uuid(details.get("workflow_id"))
    if workflow_id is not None and await db.get(Workflow, workflow_id) is None:
        return None, "workflow_missing"

    note = stored_note(row)
    facts = note.facts
    if note.kind == "policy":
        if "table" in facts:
            return None, "rows_not_stored"
        if "solution_id" not in facts:
            return None, "solution_not_recorded"
        principal = await load_policy_principal(db, row.user_id, workflow_id)
        if principal is None:
            return None, "run_user_missing"
        organization_id = note.target if isinstance(note.target, UUID) else None
        facts["model"] = await FilePolicyService(db).is_allowed(
            facts["action"],
            organization_id=organization_id,
            location=facts["location"],
            path=facts["path"],
            user=principal,
            solution_id=_uuid(facts["solution_id"]),
        )
    elif note.kind == "entry":
        kind, _, entity_id = facts["subject"].partition(":")
        facts["allowed"] = await run_user_may_open(db, _ENTRY_REPOSITORIES[kind], row.user_id, UUID(entity_id))

    powers = await load_powers(db, workflow_id)
    operation = cast("dict[str, Any]", details["inputs"])["operation"]
    return judge(run_user, powers, note, entry_for_key(operation)), None


async def test_access(
    db: AsyncSession, subject_id: UUID, target: UUID | None, entry: AccessEntry, workflow_id: UUID | None
) -> Trace:
    """What the model decides for ``subject_id`` performing ``entry`` in
    ``target``, directly or through ``workflow_id``. The caller has checked
    the subject exists."""
    run_user = await load_run_user(db, subject_id)
    assert run_user is not None, "the caller checks the subject exists"
    powers = None if workflow_id is None else await load_powers(db, workflow_id)
    workflow_access = (
        None
        if workflow_id is None or run_user.identity_kind
        else await run_user_may_open(db, WorkflowRepository, subject_id, workflow_id)
    )
    return check_operation(run_user, powers, target, entry, workflow_access=workflow_access)
