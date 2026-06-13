"""Rewrite entity references when a workflow is renamed (v1→v2 migration).

The dependency walker (``dependency_walker.py``) FINDS the string references a
workspace makes to a workflow; this module REWRITES them when the migration
renames the workflow to the org convention (``{domain}_{verb}_{noun}``).

A rename changes a workflow's identity along up to three axes:

- ``path``           — the file moves to flat ``workflows/{new}.py``,
- ``function_name``  — the Python function is renamed,
- ``name``           — the MCP/display name (used by bare-name refs).

References keyed on those must all be rewritten or the migrated app and its
forms point at a workflow that no longer exists. Two ref FORMS are rewritten:

- portable ``path::function_name`` — the canonical workflow ref,
- bare ``name`` — accepted by ``useWorkflow("name")`` and stored in form fields.

FK references (an ``AgentTool.workflow_id`` UUID, a ``Form.workflow_id`` holding
a UUID) survive a rename untouched — the row id is stable — so this rewriter
deliberately passes UUIDs and unrecognised strings through unchanged.

The source rewrite matches ``useWorkflow``/``useWorkflowQuery``/
``useWorkflowMutation`` string LITERALS (same surface ``ref_scanner`` scans), so
it is symmetric with detection: anything the walker can warn about, the rewriter
can fix. It is static — computed refs (a workflow ref built from a variable) are
invisible and must be fixed by hand, same caveat as the walker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowRename:
    """One workflow's identity change across a migration rename."""

    old_path: str
    old_function_name: str
    old_name: str
    new_path: str
    new_function_name: str
    new_name: str

    @property
    def old_ref(self) -> str:
        return f"{self.old_path}::{self.old_function_name}"

    @property
    def new_ref(self) -> str:
        return f"{self.new_path}::{self.new_function_name}"


def remap_ref_string(ref: str, renames: list[WorkflowRename]) -> str:
    """Map ONE workflow ref string through the rename set.

    Handles both ref forms (``path::fn`` and bare ``name``). An unrecognised
    string — a UUID FK ref, or a ref to a workflow that isn't being renamed —
    passes through unchanged.
    """
    for r in renames:
        if ref == r.old_ref:
            return r.new_ref
        if ref == r.old_name:
            return r.new_name
    return ref


# ``useWorkflow``/``useWorkflowQuery``/``useWorkflowMutation`` with a single
# string-literal first arg — the same hook surface ``ref_scanner`` scans. Group
# 1 = call + open paren + quote, group 2 = inner ref, group 3 = closing quote.
_HOOK_CALL_RE = re.compile(
    r"""(\buseWorkflow(?:Query|Mutation)?\s*\(\s*(['"]))([^'"]+)(['"])"""
)


def rewrite_source_refs(source: str, renames: list[WorkflowRename]) -> str:
    """Rewrite every ``useWorkflow*("ref")`` literal in ``source`` per renames.

    Whole-literal match only — ``"sync_orders_extra"`` is never rewritten by a
    ``sync_orders`` rename. Quote style is preserved.
    """

    def _sub(m: re.Match[str]) -> str:
        prefix, ref, close_q = m.group(1), m.group(3), m.group(4)
        return f"{prefix}{remap_ref_string(ref, renames)}{close_q}"

    return _HOOK_CALL_RE.sub(_sub, source)


@dataclass
class RefRewriteResult:
    """What ``apply_workflow_renames`` touched, for a migration report."""

    forms_updated: int = 0
    source_files_updated: int = 0
    updated_paths: list[str] | None = None


class WorkflowRefRewriter:
    """Apply a set of workflow renames across forms + workspace source.

    Rewrites, within the given org scope (``solution_id IS NULL`` — the loose
    ``_repo/`` universe a migration operates on, never solution-owned rows):

    - **Forms**: the ``workflow_path``/``workflow_function_name`` structured pair
      and the ``workflow_id``/``launch_workflow_id`` string fields (when they
      hold a ``path::fn`` or bare-name ref; UUIDs pass through).
    - **Source files**: ``useWorkflow*("ref")`` literals in app TSX and sibling
      workflow ``.py`` files under ``_repo/``.

    Caller is responsible for the workflow ROW updates (path/function_name/name)
    and the actual file MOVE — this rewrites the references TO those workflows so
    they keep resolving after the move. Static scan only (see module docstring).
    """

    def __init__(self, db, repo=None):
        from src.services.repo_storage import RepoStorage

        self.db = db
        self.repo = repo or RepoStorage()

    async def apply(
        self, org_id, renames: list[WorkflowRename], *, source_paths: list[str]
    ) -> RefRewriteResult:
        from sqlalchemy import select

        from src.models.orm.forms import Form

        forms_updated = await self._rewrite_forms(org_id, renames, Form, select)
        files = await self._rewrite_sources(renames, source_paths)
        return RefRewriteResult(
            forms_updated=forms_updated,
            source_files_updated=len(files),
            updated_paths=files,
        )

    async def _rewrite_forms(self, org_id, renames, Form, select) -> int:
        scope = Form.organization_id.is_(None) if org_id is None else (
            Form.organization_id == org_id
        )
        forms = (
            await self.db.execute(
                select(Form).where(Form.solution_id.is_(None), scope)
            )
        ).scalars().all()
        count = 0
        for form in forms:
            changed = False
            for attr in ("workflow_id", "launch_workflow_id"):
                cur = getattr(form, attr)
                if cur:
                    new = remap_ref_string(str(cur), renames)
                    if new != cur:
                        setattr(form, attr, new)
                        changed = True
            # The structured path/function pair: a rename of that exact pair
            # repoints it to the new file/function.
            if form.workflow_path and form.workflow_function_name:
                pair = f"{form.workflow_path}::{form.workflow_function_name}"
                for r in renames:
                    if pair == r.old_ref:
                        form.workflow_path = r.new_path
                        form.workflow_function_name = r.new_function_name
                        changed = True
                        break
            if changed:
                count += 1
        if count:
            await self.db.flush()
        return count

    async def _rewrite_sources(
        self, renames: list[WorkflowRename], source_paths: list[str]
    ) -> list[str]:
        updated: list[str] = []
        for path in source_paths:
            try:
                src = (await self.repo.read(path)).decode("utf-8")
            except Exception:
                continue  # absent / binary — nothing to rewrite
            new_src = rewrite_source_refs(src, renames)
            if new_src != src:
                await self.repo.write(path, new_src.encode("utf-8"))
                updated.append(path)
        return updated
