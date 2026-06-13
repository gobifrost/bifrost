"""WorkflowRefRewriter.apply rewrites form refs + source files on rename.

The string core is covered by ``test_solution_ref_rewriter``; this proves the
DB/repo applier: loose same-scope forms get their workflow refs repointed, a
UUID FK ref is left alone, and source files are rewritten via RepoStorage.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.forms import Form
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solutions.ref_rewriter import WorkflowRefRewriter, WorkflowRename

pytestmark = pytest.mark.e2e

RENAME = WorkflowRename(
    old_path="legacy/orders/sync.py", old_function_name="run", old_name="sync_orders",
    new_path="workflows/orders_sync_records.py",
    new_function_name="orders_sync_records", new_name="orders_sync_records",
)


class _FakeRepo:
    """In-memory RepoStorage stand-in (read/write bytes by path)."""

    def __init__(self, files: dict[str, str]):
        self.files = {k: v.encode() for k, v in files.items()}
        self.writes: list[str] = []

    async def read(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write(self, path: str, content: bytes) -> str:
        self.files[path] = content
        self.writes.append(path)
        return path


async def test_apply_rewrites_form_refs_and_sources(db_session):
    db = db_session
    # A form whose structured pair points at the renamed workflow.
    f_pair = Form(
        id=uuid.uuid4(), name=f"f-pair-{uuid.uuid4().hex[:6]}",
        organization_id=None, solution_id=None, created_by="dev@x",
        workflow_path="legacy/orders/sync.py", workflow_function_name="run",
    )
    # A form referencing by bare name in the string id field.
    f_name = Form(
        id=uuid.uuid4(), name=f"f-name-{uuid.uuid4().hex[:6]}",
        organization_id=None, solution_id=None, created_by="dev@x", workflow_id="sync_orders",
    )
    # A form pointing at an UNRELATED workflow by UUID — must be untouched.
    untouched_uuid = str(uuid.uuid4())
    f_uuid = Form(
        id=uuid.uuid4(), name=f"f-uuid-{uuid.uuid4().hex[:6]}",
        organization_id=None, solution_id=None, created_by="dev@x", workflow_id=untouched_uuid,
    )
    db.add_all([f_pair, f_name, f_uuid])
    await db.flush()

    repo = _FakeRepo({
        "apps/orders/pages/index.tsx": 'useWorkflow("legacy/orders/sync.py::run");',
        "apps/orders/pages/other.tsx": 'useWorkflow("unrelated");',
    })
    rewriter = WorkflowRefRewriter(db, repo=repo)
    result = await rewriter.apply(
        None, [RENAME],
        source_paths=["apps/orders/pages/index.tsx", "apps/orders/pages/other.tsx"],
    )

    await db.refresh(f_pair)
    await db.refresh(f_name)
    await db.refresh(f_uuid)
    assert f_pair.workflow_path == "workflows/orders_sync_records.py"
    assert f_pair.workflow_function_name == "orders_sync_records"
    assert f_name.workflow_id == "orders_sync_records"
    assert f_uuid.workflow_id == untouched_uuid  # FK ref survives

    assert result.forms_updated == 2
    assert result.source_files_updated == 1
    assert repo.writes == ["apps/orders/pages/index.tsx"]
    assert "orders_sync_records" in repo.files["apps/orders/pages/index.tsx"].decode()


async def test_apply_skips_solution_owned_and_other_org_forms(db_session):
    db = db_session
    other_org = uuid.uuid4()
    sol = uuid.uuid4()
    db.add(Solution(id=sol, slug=f"s-{uuid.uuid4().hex[:6]}", name="S", organization_id=None))
    db.add(Organization(id=other_org, name=f"org-{uuid.uuid4().hex[:6]}", created_by="dev@x"))
    await db.flush()
    # Solution-owned form — migration must not touch owned rows.
    owned = Form(
        id=uuid.uuid4(), name=f"owned-{uuid.uuid4().hex[:6]}",
        organization_id=None, solution_id=sol, created_by="dev@x", workflow_id="sync_orders",
    )
    # Different-org loose form — out of scope for an org=None rewrite.
    other = Form(
        id=uuid.uuid4(), name=f"other-{uuid.uuid4().hex[:6]}",
        organization_id=other_org, solution_id=None, created_by="dev@x", workflow_id="sync_orders",
    )
    db.add_all([owned, other])
    await db.flush()

    rewriter = WorkflowRefRewriter(db, repo=_FakeRepo({}))
    result = await rewriter.apply(None, [RENAME], source_paths=[])

    await db.refresh(owned)
    await db.refresh(other)
    assert owned.workflow_id == "sync_orders"  # untouched (owned)
    assert other.workflow_id == "sync_orders"  # untouched (other org)
    assert result.forms_updated == 0
