"""Kitchen-sink workspace import: every advertised definition through real boundaries.

The fixture is a representative Solution package (workflows + modules, a
source-backed app, tables, an inline form, an inline agent with tool bindings,
multi-type config declarations, schedule + webhook events, file policies,
ordinary files, README/metadata) plus package-only declarations (custom claims,
connection schemas, file locations, role bindings) that must warn explicitly.

The same tree exercises all four destination/source combinations:

1. workspace from ZIP (preview + durable ``workspace.bundle_import`` job);
2. workspace from repository snapshot (same plan, same job);
3. managed Solution from ZIP (isolated install lifecycle);
4. managed Solution from repository (git-connected install lifecycle).

Only the workspace cases may leave reviewable dirty ``_repo`` changes, and
only the managed cases may create a Solution record.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.e2e

FIXTURE_SLUG = "kitchen-sink"

WF_ALPHA_PATH = "workflows/sink_alpha.py"
WF_ALPHA_FN = "sink_alpha"
WF_BETA_PATH = "workflows/sink_beta.py"
WF_BETA_FN = "sink_beta"
WF_TOOL_PATH = "workflows/sink_tool.py"
WF_TOOL_FN = "sink_lookup"
APP_SLUG = "sink-board"
APP_PATH = "apps/sink-board"
TABLE_ITEMS = "sink_items"
TABLE_AUDIT = "sink_audit"
FORM_NAME = "sink_intake"
AGENT_NAME = "sink_triage"
EVENT_SCHED = "sink_nightly"
EVENT_HOOK = "sink_hook"
FP_LOCATION = "workspace"
FP_PATH = "sink-uploads"
RUNBOOK_PATH = "docs/runbook.md"

ALPHA_ID = "11111111-1111-4111-8111-111111111111"
BETA_ID = "22222222-2222-4222-8222-222222222222"
TOOL_ID = "33333333-3333-4333-8333-333333333333"
APP_ID = "44444444-4444-4444-8444-444444444444"
TABLE_ITEMS_ID = "55555555-5555-4555-8555-555555555555"
TABLE_AUDIT_ID = "55555555-5555-4555-8555-555555555556"
FORM_ID = "66666666-6666-4666-8666-666666666666"
AGENT_ID = "77777777-7777-4777-8777-777777777777"

# Mirrors examples/workspace-import-kitchen-sink/ file-for-file. The committed
# tree is the human/live-review artifact; this dict is the hermetic copy the
# test stack can reach (the runner cannot mount examples/).
FILES: dict[str, str] = {
    "bifrost.solution.yaml": (
        "slug: {slug}\nname: Workspace Import Kitchen Sink\nversion: 1.0.0\n"
        "allow_outbound_access: false\nallow_inbound_access: false\n"
    ),
    "README.md": (
        "# Workspace Import Kitchen Sink\n\nRepresentative Solution package.\n"
    ),
    "workflows/sink_alpha.py": (
        '"""Kitchen-sink alpha workflow (imported v2 definition)."""\n\n\n'
        "def sink_alpha(payload: dict | None = None) -> dict:\n"
        '    """Process one intake payload (v2)."""\n'
        "    payload = payload or {}\n"
        '    return {"status": "processed-v2", "echo": payload}\n'
    ),
    "workflows/sink_beta.py": (
        '"""Kitchen-sink beta workflow (stable definition shared with the seed)."""\n\n\n'
        "def sink_beta() -> dict:\n"
        '    """Return the beta health payload."""\n'
        '    return {"status": "beta-ok"}\n'
    ),
    "workflows/sink_tool.py": (
        '"""Kitchen-sink lookup tool used by the triage agent."""\n\n\n'
        "def sink_lookup(query: str) -> dict:\n"
        '    """Look up one record by query string."""\n'
        '    return {"query": query, "hits": []}\n'
    ),
    "workflows/sink_helpers.py": (
        '"""Shared helpers carried as an ordinary Python module (no workflow row)."""\n\n'
        'SINK_VERSION = "1.0.0"\n\n\n'
        "def normalize_name(value: str) -> str:\n"
        '    """Normalize one display name."""\n'
        '    return " ".join(value.split()).strip()\n'
    ),
    ".bifrost/workflows.yaml": (
        "workflows:\n"
        f"  {ALPHA_ID}:\n    id: {ALPHA_ID}\n    name: Sink Alpha\n"
        f"    path: {WF_ALPHA_PATH}\n    function_name: {WF_ALPHA_FN}\n"
        "    description: Alpha v2 (imported)\n    role_names:\n      - Viewer\n"
        f"  {BETA_ID}:\n    id: {BETA_ID}\n    name: Sink Beta\n"
        f"    path: {WF_BETA_PATH}\n    function_name: {WF_BETA_FN}\n"
        "    description: Beta\n"
        f"  {TOOL_ID}:\n    id: {TOOL_ID}\n    name: Sink Lookup\n"
        f"    path: {WF_TOOL_PATH}\n    function_name: {WF_TOOL_FN}\n"
        "    type: tool\n    description: Lookup tool for the triage agent\n"
    ),
    ".bifrost/apps.yaml": (
        "apps:\n"
        f"  {APP_ID}:\n    id: {APP_ID}\n    slug: {APP_SLUG}\n"
        "    name: Sink Board\n    description: Sink board v2\n"
        f"    path: {APP_PATH}\n    access_level: authenticated\n"
        "    app_model: standalone_v2\n    dependencies:\n      lodash: ^4.17.21\n"
        "    dist_files:\n"
        '      index.html: "<!doctype html><html><body><h1>Sink Board</h1></body></html>"\n'
    ),
    "apps/sink-board/App.tsx": (
        'import "./styles.css";\nimport { template } from "./main";\n\n'
        "export default function SinkBoard(): string {\n"
        '  return template("Sink Board v2");\n}\n'
    ),
    "apps/sink-board/main.ts": (
        'import { join } from "lodash";\n\n'
        "export function template(title: string): string {\n"
        '  return `<main class="sink-board"><h1>${join([title], "")}</h1></main>`;\n'
        "}\n\n"
        'export const appName = "sink-board";\n'
    ),
    "apps/sink-board/styles.css": (
        ".sink-board {\n  margin: 0 auto;\n  max-width: 48rem;\n"
        "  font-family: sans-serif;\n}\n"
    ),
    ".bifrost/tables.yaml": (
        "tables:\n"
        f"  {TABLE_ITEMS_ID}:\n    id: {TABLE_ITEMS_ID}\n    name: {TABLE_ITEMS}\n"
        "    schema:\n      columns:\n"
        "        - name: title\n          type: text\n"
        "        - name: qty\n          type: integer\n"
        f"  {TABLE_AUDIT_ID}:\n    id: {TABLE_AUDIT_ID}\n    name: {TABLE_AUDIT}\n"
        "    description: Append-only audit log\n    schema:\n      columns:\n"
        "        - name: event\n          type: text\n"
        "        - name: at\n          type: text\n"
    ),
    ".bifrost/forms.yaml": (
        "forms:\n"
        f"  {FORM_ID}:\n    id: {FORM_ID}\n    name: {FORM_NAME}\n"
        "    description: Intake form v2\n"
        '    confirmation_markdown: "## Received\\n\\nWe will be in touch."\n'
        f"    workflow_id: {ALPHA_ID}\n"
        "    form_schema:\n      fields:\n"
        "        - name: email\n          type: text\n"
        "          required: true\n          label: Email\n"
        "        - name: count\n          type: number\n"
        "          required: false\n          label: Count\n          default_value: 5\n"
    ),
    ".bifrost/agents.yaml": (
        "agents:\n"
        f"  {AGENT_ID}:\n    id: {AGENT_ID}\n    name: {AGENT_NAME}\n"
        "    description: Triage incoming sink tickets v2\n"
        "    system_prompt: You are a triage agent. Classify sink tickets quickly.\n"
        "    channels:\n      - chat\n    tool_ids:\n"
        f"      - {TOOL_ID}\n"
        "    system_tools:\n      - execute_workflow\n"
        "    llm_max_tokens: 8000\n    max_iterations: 15\n"
    ),
    ".bifrost/configs.yaml": (
        "configs:\n"
        "  SINK_API_URL:\n"
        "    id: e1111111-1111-4111-8111-111111111111\n"
        "    key: SINK_API_URL\n    type: string\n    required: true\n"
        "    description: Base URL for the sink API\n"
        "    default: https://api.example.invalid\n    position: 0\n"
        "  SINK_RETRIES:\n"
        "    id: e2222222-2222-4222-8222-222222222222\n"
        "    key: SINK_RETRIES\n    type: int\n    required: false\n"
        "    description: Retry budget v2\n    default: \"3\"\n    position: 1\n"
        "  SINK_VERBOSE:\n"
        "    id: e3333333-3333-4333-8333-333333333333\n"
        "    key: SINK_VERBOSE\n    type: bool\n    required: false\n"
        "    description: Verbose logging\n    default: \"false\"\n    position: 2\n"
        "  SINK_TOKEN:\n"
        "    id: e4444444-4444-4333-8333-444444444444\n"
        "    key: SINK_TOKEN\n    type: secret\n    required: true\n"
        "    description: Sink API token\n    default: null\n    position: 3\n"
    ),
    ".bifrost/events.yaml": (
        "events:\n"
        "  88888888-8888-4888-8888-888888888888:\n"
        "    id: 88888888-8888-4888-8888-888888888888\n"
        f"    name: {EVENT_SCHED}\n    source_type: schedule\n"
        "    cron_expression: 0 9 * * *\n    timezone: America/New_York\n"
        "    subscriptions:\n"
        "      - id: aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa\n"
        "        target_type: workflow\n"
        f"        workflow_id: {ALPHA_ID}\n"
        "  99999999-9999-4999-8999-999999999999:\n"
        "    id: 99999999-9999-4999-8999-999999999999\n"
        f"    name: {EVENT_HOOK}\n    source_type: webhook\n"
        "    adapter_name: generic\n    subscriptions:\n"
        "      - id: bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb\n"
        "        target_type: workflow\n"
        f"        workflow_id: {TOOL_ID}\n"
        "        event_type: sink.ping\n"
    ),
    ".bifrost/file-policies.yaml": (
        "file_policies:\n"
        "  dddddddd-dddd-4ddd-8ddd-dddddddddddd:\n"
        "    id: dddddddd-dddd-4ddd-8ddd-dddddddddddd\n"
        f"    location: {FP_LOCATION}\n    path: {FP_PATH}\n"
        "    policies:\n      - name: sink-read\n        actions:\n          - read\n"
    ),
    ".bifrost/claims.yaml": (
        "claims:\n"
        "  cccccccc-cccc-4ccc-8ccc-cccccccccccc:\n"
        "    id: cccccccc-cccc-4ccc-8ccc-cccccccccccc\n"
        "    name: sink_vip\n    description: Flags VIP sink customers\n"
        "    type: list\n    query:\n      table: sink_items\n"
        '      where: {gt: [{row: qty}, 100]}\n      select: title\n'
    ),
    ".bifrost/connections.yaml": (
        "connections:\n  acme:\n    integration_name: acme\n"
        "    template: {}\n    position: 0\n"
    ),
    ".bifrost/files.yaml": "locations:\n  - sink-archive\n",
    "docs/runbook.md": (
        "# Sink runbook (ordinary managed source file)\n\n"
        "Review imported workspace content here before committing it. Run the\n"
        "compatibility checks, verify rewritten references, then commit or discard.\n"
    ),
}

OLD_ALPHA_PY = FILES[WF_ALPHA_PATH].replace("processed-v2", "processed-v1")
OLD_APP_TSX = FILES[f"{APP_PATH}/App.tsx"].replace("Sink Board v2", "Sink Board v1")

_SHARED_ROOT = Path("/tmp/bifrost/solution-repo-fixtures")
_CREATED: list[Path] = []


@pytest.fixture(autouse=True)
def _cleanup_shared():
    yield
    while _CREATED:
        shutil.rmtree(_CREATED.pop(), ignore_errors=True)


def _write_tree(root: Path, slug: str = FIXTURE_SLUG, app_slug: str = APP_SLUG) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content.replace("slug: {slug}", f"slug: {slug}")
        if rel == ".bifrost/apps.yaml":
            # Managed copies need a unique app slug: /apps/{slug} is globally
            # unique across workspace and installed apps.
            text = text.replace(f"slug: {APP_SLUG}", f"slug: {app_slug}")
        path.write_text(text)
    return root


def _zip_bytes(root: Path) -> bytes:
    from bifrost.commands.solution import _build_deploy_zip

    return _build_deploy_zip(root, extra_text_files={})


def _git_repo(root: Path) -> tuple[str, str]:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return f"file://{root}", out.stdout.strip()


def _stage_source_tree(
    slug: str = FIXTURE_SLUG, app_slug: str = APP_SLUG,
) -> tuple[Path, bytes, str, str]:
    """Write the fixture tree to the shared mount; return (root, zip, repo_url, commit)."""
    _SHARED_ROOT.mkdir(parents=True, exist_ok=True)
    root = _SHARED_ROOT / f"kitchen-{uuid.uuid4().hex[:8]}"
    _CREATED.append(root)
    _write_tree(root, slug, app_slug)
    archive = _zip_bytes(root)
    repo_url, commit = _git_repo(root)
    return root, archive, repo_url, commit


def _bare_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _preview_zip(e2e_client, headers: dict[str, str], archive: bytes) -> dict:
    response = e2e_client.post(
        "/api/solutions/import-workspace/preview",
        headers=_bare_headers(headers),
        files={"file": ("kitchen-sink.zip", archive, "application/zip")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _preview_repo(e2e_client, headers: dict[str, str], repo_url: str) -> dict:
    response = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=headers,
        json={"repo_url": repo_url},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _items_by_match(preview: dict) -> dict[str, dict]:
    return {item.get("match_key") or item["id"]: item for item in preview["items"]}


def _key(*parts: object) -> str:
    """The review-table form of a natural key (mirrors the planner)."""
    return " :: ".join(str(part) for part in parts if part is not None)


def _decide(preview: dict, action: str, *, keep: set[str] | None = None) -> list[dict]:
    keep = keep or set()
    return [
        {"item_id": item["id"], "action": "keep" if item["id"] in keep else action}
        for item in preview["items"]
        if item["classification"] == "conflict"
    ]


def _enqueue(e2e_client, headers: dict[str, str], preview: dict, decisions: list[dict]) -> str:
    response = e2e_client.post(
        "/api/solutions/import-workspace",
        headers=headers,
        json={"preview_token": preview["preview_token"], "decisions": decisions},
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def _wait_job(e2e_client, headers: dict[str, str], job_id: str, timeout: float = 90) -> dict:
    job: dict = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.25)
    assert job.get("status") == "succeeded", {
        "status": job.get("status"),
        "error": job.get("error"),
        "result": job.get("result"),
    }
    return job


async def _seed_destination(db_session) -> dict[str, object]:
    """Idempotently seed older/conflicting destination content (unattached rows)."""
    from src.models.enums import ConfigType
    from src.models.orm.agents import Agent
    from src.models.orm.applications import Application
    from src.models.orm.config import Config
    from src.models.orm.file_metadata import FilePolicy
    from src.models.orm.forms import Form
    from src.models.orm.tables import Table
    from src.models.orm.workflows import Workflow
    from src.services.repo_storage import RepoStorage

    async def _upsert(model, key: dict, values: dict):
        row = (await db_session.execute(select(model).filter_by(**key))).scalars().first()
        if row is None:
            row = model(**{**key, **values})
            db_session.add(row)
        else:
            for attr, value in values.items():
                setattr(row, attr, value)
        return row

    alpha = await _upsert(
        Workflow,
        {"path": WF_ALPHA_PATH, "function_name": WF_ALPHA_FN},
        {"name": "Sink Alpha", "description": "Alpha v1 (existing)",
         "type": "workflow", "access_level": "authenticated",
         "organization_id": None, "solution_id": None},
    )
    await _upsert(
        Workflow,
        {"path": WF_BETA_PATH, "function_name": WF_BETA_FN},
        {"name": "Sink Beta", "description": "Beta",
         "type": "workflow", "access_level": "authenticated",
         "organization_id": None, "solution_id": None},
    )
    await _upsert(
        Table,
        {"name": TABLE_ITEMS},
        {"schema": {"columns": [
            {"name": "title", "type": "text"}, {"name": "qty", "type": "integer"}]},
         "organization_id": None, "solution_id": None},
    )
    await _upsert(
        Form,
        {"name": FORM_NAME},
        {"description": "Intake form v1", "created_by": "kitchen-sink-seed",
         "organization_id": None, "solution_id": None},
    )
    await _upsert(
        Agent,
        {"name": AGENT_NAME},
        {"description": "Triage incoming sink tickets v1",
         "system_prompt": "Old triage prompt.",
         "created_by": "kitchen-sink-seed",
         "organization_id": None, "solution_id": None},
    )
    await _upsert(
        Config,
        {"key": "SINK_API_URL", "organization_id": None, "integration_id": None},
        {"config_type": ConfigType.STRING, "value": "https://api.example.invalid",
         "description": "Base URL v1", "updated_by": "kitchen-sink-seed"},
    )
    await _upsert(
        Config,
        {"key": "SINK_RETRIES", "organization_id": None, "integration_id": None},
        {"config_type": ConfigType.INT, "value": 3, "description": "Retry budget v1",
         "updated_by": "kitchen-sink-seed"},
    )
    await _upsert(
        Config,
        {"key": "SINK_TOKEN", "organization_id": None, "integration_id": None},
        {"config_type": ConfigType.SECRET, "value": {"value": "live-token"},
         "description": "Sink API token v1", "updated_by": "kitchen-sink-seed"},
    )
    await _upsert(
        Application,
        {"slug": APP_SLUG},
        {"name": "Sink Board", "repo_path": APP_PATH,
         "description": "Sink board v1", "dependencies": {"lodash": "^4.17.21"},
         "access_level": "authenticated", "app_model": "standalone_v2",
         "organization_id": None, "solution_id": None},
    )
    await _upsert(
        FilePolicy,
        {"location": FP_LOCATION, "path": FP_PATH},
        {"policies": {"policies": [{"name": "sink-write", "actions": ["write"]}]},
         "organization_id": None, "solution_id": None},
    )
    await db_session.commit()

    repo = RepoStorage()
    await repo.write(WF_ALPHA_PATH, OLD_ALPHA_PY.encode())
    await repo.write(WF_BETA_PATH, FILES[WF_BETA_PATH].encode())
    await repo.write(RUNBOOK_PATH, FILES[RUNBOOK_PATH].encode())
    await repo.write(f"{APP_PATH}/App.tsx", OLD_APP_TSX.encode())
    return {"alpha_id": alpha.id, "alpha_created_at": alpha.created_at}


async def _replace_all_import(e2e_client, headers, archive: bytes) -> tuple[dict, dict]:
    preview = _preview_zip(e2e_client, headers, archive)
    job_id = _enqueue(e2e_client, headers, preview, _decide(preview, "replace"))
    _wait_job(e2e_client, headers, job_id)
    return preview, _items_by_match(preview)


async def test_workspace_zip_preview_classifies_every_kind(
    e2e_client, platform_admin, db_session,
) -> None:
    """One seeded preview contains creates, unchanged, conflicts, and warnings."""
    _, archive, _, _ = _stage_source_tree()
    await _seed_destination(db_session)
    preview = _preview_zip(e2e_client, platform_admin.headers, archive)

    assert preview["source_kind"] == "zip"
    assert len(preview["package_sha256"]) == 64
    by_match = _items_by_match(preview)
    kinds = {item["classification"] for item in preview["items"]}
    assert {"create", "unchanged", "conflict"} <= kinds

    # Entity conflicts keep their destination identity for the decision UI.
    alpha = by_match[_key(WF_ALPHA_PATH, WF_ALPHA_FN)]
    assert alpha["classification"] == "conflict"
    assert alpha["kind"] == "workflow"
    assert alpha["target_id"] is not None
    beta = by_match[_key(WF_BETA_PATH, WF_BETA_FN)]
    assert beta["classification"] == "unchanged"
    assert beta["target_id"] is not None
    # The tool workflow, audit table, and events are new.
    assert by_match[_key(WF_TOOL_PATH, WF_TOOL_FN)]["classification"] == "create"
    assert by_match[_key(TABLE_AUDIT)]["classification"] == "create"
    assert by_match[_key(EVENT_SCHED)]["classification"] == "create"
    assert by_match[_key(EVENT_HOOK)]["classification"] == "create"
    # The app and file policy collide on natural keys and keep destination IDs.
    assert by_match[_key(APP_SLUG)]["classification"] == "conflict"
    assert by_match[_key(FP_LOCATION, FP_PATH)]["classification"] == "conflict"
    # Verbose config is new; seeded configs conflict on their v1 descriptions.
    assert by_match[_key("SINK_VERBOSE")]["classification"] == "create"
    assert by_match[_key("SINK_TOKEN")]["classification"] == "conflict"
    # Files: replaced alpha, identical beta/runbook, new remainder.
    assert by_match[WF_ALPHA_PATH]["classification"] == "conflict"
    assert by_match[WF_BETA_PATH]["classification"] == "unchanged"
    assert by_match[RUNBOOK_PATH]["classification"] == "unchanged"
    assert by_match[f"{APP_PATH}/App.tsx"]["classification"] == "conflict"
    # Definitions link to the files implementing them for combined decisions.
    assert alpha["group_key"] is not None
    assert alpha["group_key"] == by_match[WF_ALPHA_PATH]["group_key"]
    app_item = by_match[_key(APP_SLUG)]
    assert app_item["group_key"] is not None
    assert app_item["group_key"] == by_match[f"{APP_PATH}/App.tsx"]["group_key"]
    assert by_match[RUNBOOK_PATH]["group_key"] is None

    warnings = preview["warnings"]
    assert any("unattached" in w for w in warnings)
    assert any("claims" in w.lower() for w in warnings)
    assert any("connection" in w.lower() for w in warnings)
    assert any("file-location" in w.lower() for w in warnings)
    assert any("role" in w.lower() for w in warnings)
    assert any("config" in w.lower() for w in warnings)
    # Package-only declarations never become review items.
    assert {item["kind"] for item in preview["items"]} <= {
        "workflow", "integration", "config", "app", "table", "event",
        "form", "agent", "claim", "policy_rule", "file_policy", "file",
    }
    assert not [item for item in preview["items"] if item["kind"] in {"claim", "policy_rule"}]

    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    await WorkspaceBundleStorage(preview["preview_token"]).delete()


async def test_workspace_zip_import_preserves_ids_rewrites_refs_and_runtime(
    e2e_client, platform_admin, db_session,
) -> None:
    """Replace-all through the durable job keeps IDs, remaps refs, keeps runtime."""
    _, archive, _, _ = _stage_source_tree()
    seeded = await _seed_destination(db_session)
    preview, _ = await _replace_all_import(e2e_client, platform_admin.headers, archive)

    from src.models.orm.agents import Agent, AgentTool
    from src.models.orm.applications import Application
    from src.models.orm.config import Config
    from src.models.orm.events import EventSource, EventSubscription
    from src.models.orm.file_metadata import FilePolicy
    from src.models.orm.forms import Form, FormField
    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow
    from src.services.repo_storage import RepoStorage

    alpha_id = seeded["alpha_id"]
    alpha = await db_session.get(Workflow, alpha_id)
    assert alpha is not None
    assert alpha.description == "Alpha v2 (imported)"
    assert alpha.created_at == seeded["alpha_created_at"]
    assert alpha.solution_id is None and alpha.organization_id is None

    beta = (
        await db_session.execute(
            select(Workflow).where(
                Workflow.path == WF_BETA_PATH, Workflow.function_name == WF_BETA_FN)
        )
    ).scalars().one()
    assert beta.description == "Beta"

    tool = (
        await db_session.execute(
            select(Workflow).where(
                Workflow.path == WF_TOOL_PATH, Workflow.function_name == WF_TOOL_FN)
        )
    ).scalars().one()
    assert tool.solution_id is None

    # Typed references point at destination IDs, not package IDs.
    form = (
        await db_session.execute(select(Form).where(Form.name == FORM_NAME))
    ).scalars().all()[-1]
    assert form.workflow_id == str(alpha_id)
    assert form.solution_id is None
    fields = (
        await db_session.execute(select(FormField).where(FormField.form_id == form.id))
    ).scalars().all()
    assert {f.name for f in fields} >= {"email", "count"}

    agent = (
        await db_session.execute(select(Agent).where(Agent.name == AGENT_NAME))
    ).scalars().all()[-1]
    tool_links = (
        await db_session.execute(select(AgentTool).where(AgentTool.agent_id == agent.id))
    ).scalars().all()
    assert {str(link.workflow_id) for link in tool_links} >= {str(tool.id)}

    sched_sources = (
        await db_session.execute(select(EventSource).where(EventSource.name == EVENT_SCHED))
    ).scalars().all()
    assert sched_sources, "schedule event source was not imported"
    sched_subs = (
        await db_session.execute(
            select(EventSubscription).where(
                EventSubscription.event_source_id.in_([s.id for s in sched_sources]))
        )
    ).scalars().all()
    assert {str(sub.workflow_id) for sub in sched_subs} >= {str(alpha_id)}
    hook_sources = (
        await db_session.execute(select(EventSource).where(EventSource.name == EVENT_HOOK))
    ).scalars().all()
    assert hook_sources, "webhook event source was not imported"

    # Runtime-owned state survives replacement.
    token = (
        await db_session.execute(select(Config).where(Config.key == "SINK_TOKEN"))
    ).scalars().all()[-1]
    assert token.value == {"value": "live-token"}
    assert token.description == "Sink API token v1"
    retries = (
        await db_session.execute(select(Config).where(Config.key == "SINK_RETRIES"))
    ).scalars().all()[-1]
    assert retries.description == "Retry budget v2"

    policy = (
        await db_session.execute(
            select(FilePolicy).where(
                FilePolicy.location == FP_LOCATION, FilePolicy.path == FP_PATH)
        )
    ).scalars().all()[-1]
    assert policy.solution_id is None

    app = (
        await db_session.execute(select(Application).where(Application.slug == APP_SLUG))
    ).scalars().all()[-1]
    assert app.description == "Sink board v2"
    assert app.solution_id is None

    repo = RepoStorage()
    assert await repo.read(WF_ALPHA_PATH) == FILES[WF_ALPHA_PATH].encode()
    assert await repo.read(f"{APP_PATH}/App.tsx") == FILES[f"{APP_PATH}/App.tsx"].encode()
    manifest = await repo.read(".bifrost/workflows.yaml")
    assert WF_ALPHA_FN.encode() in manifest

    assert (
        await db_session.execute(select(Solution).where(Solution.slug == FIXTURE_SLUG))
    ).scalars().all() == []

    # Dirty state is asserted server-side: an in-process redis read would reuse
    # a connection cached on an earlier test's (now closed) event loop when
    # several e2e files share one runner process.
    status = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers)
    assert status.status_code == 200, status.text
    assert status.json()["dirty"] is True


async def test_workspace_import_keep_preserves_destination_content(
    e2e_client, platform_admin, db_session,
) -> None:
    """Keep decisions leave destination definitions (and their IDs) in place."""
    _, archive, _, _ = _stage_source_tree()
    await _seed_destination(db_session)
    await _replace_all_import(e2e_client, platform_admin.headers, archive)

    from src.models.orm.forms import Form
    from src.models.orm.workflows import Workflow

    beta = (
        await db_session.execute(
            select(Workflow).where(
                Workflow.path == WF_BETA_PATH, Workflow.function_name == WF_BETA_FN)
        )
    ).scalars().one()
    beta.description = "Beta v3 local"
    await db_session.commit()

    preview = _preview_zip(e2e_client, platform_admin.headers, archive)
    by_match = _items_by_match(preview)
    assert by_match[_key(WF_BETA_PATH, WF_BETA_FN)]["classification"] == "conflict"
    keep_id = by_match[_key(WF_BETA_PATH, WF_BETA_FN)]["id"]
    job_id = _enqueue(
        e2e_client, platform_admin.headers, preview, _decide(preview, "replace", keep={keep_id})
    )
    _wait_job(e2e_client, platform_admin.headers, job_id)

    kept = await db_session.get(Workflow, beta.id)
    assert kept is not None
    assert kept.description == "Beta v3 local"
    forms = (
        await db_session.execute(select(Form).where(Form.name == FORM_NAME))
    ).scalars().all()
    assert forms, "kept import must retain usable form references"

    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    await WorkspaceBundleStorage(preview["preview_token"]).delete()


async def test_workspace_repo_import_converges_and_shares_the_job(
    e2e_client, platform_admin, db_session,
) -> None:
    """Repository snapshots plan like ZIPs and apply through the same job + lock."""
    _, archive, repo_url, commit = _stage_source_tree()
    await _seed_destination(db_session)

    zipped = _preview_zip(e2e_client, platform_admin.headers, archive)
    repo = _preview_repo(e2e_client, platform_admin.headers, repo_url)
    assert repo["source_kind"] == "repo"
    assert repo["repo_url"] == repo_url
    assert repo["resolved_commit"] == commit
    assert repo["package_sha256"] == zipped["package_sha256"]

    def _plan_key(preview: dict) -> dict[str, tuple[str, str]]:
        return {
            (item["id"].split(":", 1)[1] if ":" in item["id"] else item["id"]): (
                item["classification"], item.get("match_key") or "",
            )
            for item in preview["items"]
        }

    assert _plan_key(repo) == _plan_key(zipped)

    job_id = _enqueue(e2e_client, platform_admin.headers, repo, _decide(repo, "keep"))
    from src.jobs.platform.git_operation import WORKSPACE_MUTATION_RESOURCE_LOCK_KEY
    from src.models.orm.platform_jobs import PlatformJob

    row = await db_session.get(PlatformJob, uuid.UUID(job_id))
    assert row is not None
    assert row.resource_lock_key == WORKSPACE_MUTATION_RESOURCE_LOCK_KEY
    _wait_job(e2e_client, platform_admin.headers, job_id)

    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow

    assert (
        await db_session.execute(select(Solution).where(Solution.slug == FIXTURE_SLUG))
    ).scalars().all() == []
    alpha = (
        await db_session.execute(
            select(Workflow).where(
                Workflow.path == WF_ALPHA_PATH, Workflow.function_name == WF_ALPHA_FN)
        )
    ).scalars().all()[-1]
    assert alpha.solution_id is None

    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    await WorkspaceBundleStorage(zipped["preview_token"]).delete()


async def test_managed_solution_install_from_zip_keeps_lifecycle(
    e2e_client, platform_admin, db_session,
) -> None:
    """The same tree installs as an isolated, lifecycle-managed Solution."""
    from tests.e2e.platform.conftest import wait_for_install

    slug = f"{FIXTURE_SLUG}-{uuid.uuid4().hex[:8]}"
    _, archive, _, _ = _stage_source_tree(slug, f"{APP_SLUG}-{slug}")

    preview = e2e_client.post(
        "/api/solutions/install/preview",
        headers=_bare_headers(platform_admin.headers),
        files={"file": ("kitchen-sink.zip", archive, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["slug"] == slug
    assert len(body["workflows"]) == 3
    assert len(body["config_schemas"]) == 4
    assert len(body["connection_schemas"]) == 1

    installed = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=_bare_headers(platform_admin.headers),
            files={"file": ("kitchen-sink.zip", archive, "application/zip")},
        ),
        platform_admin.headers,
    )
    assert installed.status_code in {200, 201}, installed.text

    from src.models.orm.solutions import Solution

    solution = (
        await db_session.execute(select(Solution).where(Solution.slug == slug))
    ).scalars().one()
    assert solution.git_connected is False


async def test_managed_solution_install_from_repo_is_git_connected(
    e2e_client, platform_admin, db_session,
) -> None:
    """The same tree installs git-connected from its repository."""
    from tests.e2e.platform.conftest import wait_for_install

    slug = f"{FIXTURE_SLUG}-{uuid.uuid4().hex[:8]}"
    _, _, repo_url, _ = _stage_source_tree(slug, f"{APP_SLUG}-{slug}")

    installed = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install/from-repo",
            headers=platform_admin.headers,
            json={"repo_url": repo_url},
        ),
        platform_admin.headers,
    )
    assert installed.status_code in {200, 201}, installed.text

    from src.models.orm.solutions import Solution

    solution = (
        await db_session.execute(select(Solution).where(Solution.slug == slug))
    ).scalars().one()
    assert solution.git_connected is True
    assert solution.git_repo_url == repo_url
