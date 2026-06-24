"""Unit tests for Solution export — ``build_workspace_zip`` must serialize a
bundle into the SAME workspace shape the zip-install preview consumes, so an
export is directly re-installable (round-trip proof, no DB/S3)."""
from __future__ import annotations

import base64
import io
import uuid
import zipfile
from pathlib import Path

import pytest

from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.orm.solutions import Solution
from src.routers.solutions import export_solution
from src.services.solutions.deploy import SolutionBundle
from src.services.solutions.export import (
    add_encrypted_content_to_workspace_zip,
    build_workspace_zip,
)
from src.services.solutions.secrets_blob import SolutionContent, decode_secrets_blob
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
from src.services.solutions.zip_install import preview_zip

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode("ascii")

WF_ID = "11111111-1111-1111-1111-111111111111"
TBL_ID = "22222222-2222-2222-2222-222222222222"
APP_ID = "33333333-3333-3333-3333-333333333333"
FORM_ID = "44444444-4444-4444-4444-444444444444"
AGENT_ID = "55555555-5555-5555-5555-555555555555"


def _bundle() -> SolutionBundle:
    solution = Solution(
        slug="exp-demo",
        name="Export Demo",
        organization_id=None,
        global_repo_access=True,
    )
    return SolutionBundle(
        solution=solution,
        version="1.2.3",
        logo_b64=_PNG,
        logo_content_type="image/png",
        python_files={
            "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
            "modules/helper.py": "X = 1\n",
        },
        workflows=[
            {
                "id": WF_ID,
                "name": "main",
                "function_name": "run",
                "path": "workflows/main.py",
                "endpoint_enabled": True,
                "timeout_seconds": 60,
            }
        ],
        tables=[
            {
                "id": TBL_ID,
                "name": "things",
                "description": "demo rows",
                "schema": {"fields": [{"name": "title", "type": "string"}]},
                "policies": [{"role": "member", "action": "read"}],
            }
        ],
        apps=[
            {
                "id": APP_ID,
                "slug": "dash",
                "name": "Dash",
                "description": "the app",
                "app_model": "standalone_v2",
                "dependencies": {"react": "^18.0.0"},
                "access_level": "role_based",
                "roles": [],
                "role_names": ["Staff"],
                "logo_b64": _PNG,
                "logo_content_type": "image/png",
                "src_files": {"src/App.tsx": "export default () => null;\n"},
                "bin_files": {"public/font.woff2": base64.b64encode(b"\x00\x01binary").decode("ascii")},
            }
        ],
        forms=[{"id": FORM_ID, "name": "Intake", "fields": [{"key": "email"}]}],
        agents=[{"id": AGENT_ID, "name": "Helper", "system_prompt": "be helpful"}],
        config_schemas=[
            {
                "id": "API_KEY",
                "key": "API_KEY",
                "type": "secret",
                "required": True,
                "description": "needed",
                "default": None,
                "position": 0,
            }
        ],
        file_locations=["reports", "invoices"],
    )


def _admin(db) -> tuple[ExecutionContext, UserPrincipal]:
    user = UserPrincipal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    return ExecutionContext(user=user, org_id=None, db=db), user


def _response_bytes(response) -> bytes:  # noqa: ANN001
    path = Path(response.path)
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def test_export_round_trips_through_preview() -> None:
    data = build_workspace_zip(_bundle())
    result = preview_zip(data)

    assert result.slug == "exp-demo"
    assert result.name == "Export Demo"
    # The exported descriptor carries no install scope — kind is the installer's
    # deploy-time choice (--org/--global), derived from organization_id.
    assert result.scope is None
    assert result.version == "1.2.3"
    assert result.logo  # descriptor points at a real logo file in the zip

    assert [w["id"] for w in result.workflows] == [WF_ID]
    wf = result.workflows[0]
    assert wf["function_name"] == "run"
    assert wf["endpoint_enabled"] is True  # full body passthrough, not a subset
    assert wf["timeout_seconds"] == 60

    assert [t["id"] for t in result.tables] == [TBL_ID]
    tbl = result.tables[0]
    assert tbl["schema"] == {"fields": [{"name": "title", "type": "string"}]}
    assert tbl["policies"] == [{"role": "member", "action": "read"}]

    assert [f["id"] for f in result.forms] == [FORM_ID]
    assert result.forms[0]["fields"] == [{"key": "email"}]
    assert [a["id"] for a in result.agents] == [AGENT_ID]
    assert result.agents[0]["system_prompt"] == "be helpful"

    assert [c["key"] for c in result.config_schemas] == ["API_KEY"]
    assert result.config_schemas[0]["required"] is True
    assert result.file_locations == ["reports", "invoices"]


def test_export_apps_round_trip_source_and_logo() -> None:
    data = build_workspace_zip(_bundle())
    result = preview_zip(data)

    assert [a["id"] for a in result.apps] == [APP_ID]
    app = result.apps[0]
    assert app["slug"] == "dash"
    assert app["app_model"] == "standalone_v2"
    assert app["dependencies"] == {"react": "^18.0.0"}
    assert app["role_names"] == ["Staff"]
    # Source + binary assets survive the round trip (the preview collector
    # re-reads them from the zip's app dir).
    assert app["src_files"]["src/App.tsx"] == "export default () => null;\n"
    assert base64.b64decode(app["bin_files"]["public/font.woff2"]) == b"\x00\x01binary"
    # The app logo came back as a real file referenced by the manifest.
    assert app["logo_b64"] == _PNG
    assert app["logo_content_type"] == "image/png"


def test_export_python_source_verbatim() -> None:
    data = build_workspace_zip(_bundle())
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert "workflows/main.py" in names
        assert "modules/helper.py" in names
        assert z.read("workflows/main.py").decode() == "def run(sdk):\n    return 'ok'\n"
        # Solution logo file present at the descriptor's path.
        assert "solution-logo.png" in names


def test_export_is_deterministic() -> None:
    """Idempotent finalize retries must not churn bytes (fixed zip mtimes)."""
    assert build_workspace_zip(_bundle()) == build_workspace_zip(_bundle())


def test_export_writes_files_yaml_top_level_locations() -> None:
    with zipfile.ZipFile(io.BytesIO(build_workspace_zip(_bundle()))) as z:
        payload = z.read(".bifrost/files.yaml").decode()

    assert payload == "locations:\n- reports\n- invoices\n"


def test_export_descriptor_omits_scope_regardless_of_org() -> None:
    """The exported descriptor never carries scope — not for an org install nor
    a global one. Install kind is the installer's deploy-time choice, derived
    from organization_id, so it must not be baked into the portable definition."""
    import uuid

    b = _bundle()
    b.solution.organization_id = uuid.uuid4()
    b.solution.global_repo_access = False
    result = preview_zip(build_workspace_zip(b))
    assert result.scope is None
    # And the raw descriptor in the zip has no scope: key.
    import yaml

    with zipfile.ZipFile(io.BytesIO(build_workspace_zip(b))) as z:
        descriptor = yaml.safe_load(z.read("bifrost.solution.yaml"))
    assert "scope" not in descriptor


def test_full_export_overlays_encrypted_content_without_rebuilding_source() -> None:
    source_zip = build_workspace_zip(_bundle())

    data = add_encrypted_content_to_workspace_zip(
        source_zip,
        SolutionContent(
            config_values={"API_KEY": "secret-value"},
            table_data={"things": [{"title": "stored row"}]},
        ),
        password="pw",
    )

    assert data != source_zip
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert "apps/dash/src/App.tsx" in names
        assert "workflows/main.py" in names
        blob = z.read(".bifrost/secrets.enc").decode()

    content = decode_secrets_blob(blob, password="pw")
    assert content.config_values == {"API_KEY": "secret-value"}
    assert content.table_data == {"things": [{"title": "stored row"}]}


def test_full_export_replaces_any_existing_encrypted_content() -> None:
    source_zip = add_encrypted_content_to_workspace_zip(
        build_workspace_zip(_bundle()),
        SolutionContent(config_values={"OLD": "value"}),
        password="old",
    )

    data = add_encrypted_content_to_workspace_zip(
        source_zip,
        SolutionContent(config_values={"NEW": "value"}),
        password="new",
    )

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist().count(".bifrost/secrets.enc") == 1
        blob = z.read(".bifrost/secrets.enc").decode()
    content = decode_secrets_blob(blob, password="new")
    assert content.config_values == {"NEW": "value"}


@pytest.mark.e2e
async def test_shareable_export_returns_stored_source_artifact(db_session) -> None:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"stored-{uuid.uuid4().hex[:8]}",
        name="Stored Source",
        organization_id=None,
    )
    db_session.add(sol)
    await db_session.flush()
    source_bundle = _bundle()
    source_bundle.python_files["workflows/main.py"] = "def run():\n    return 'artifact'\n"
    source_zip = build_workspace_zip(source_bundle)
    await SolutionSourceArtifactStorage(sol.id).write(source_zip)

    ctx, user = _admin(db_session)
    response = await export_solution(sol.id, ctx, user, mode="shareable")

    assert _response_bytes(response) == source_zip


@pytest.mark.e2e
async def test_full_export_overlays_live_payload_on_stored_source(
    db_session,
    monkeypatch,
) -> None:
    from src.services.solutions.capture import SolutionCaptureService

    sol = Solution(
        id=uuid.uuid4(),
        slug=f"full-{uuid.uuid4().hex[:8]}",
        name="Full Source",
        organization_id=None,
    )
    db_session.add(sol)
    await db_session.flush()
    source_bundle = _bundle()
    source_bundle.python_files["workflows/main.py"] = "def run():\n    return 'artifact'\n"
    await SolutionSourceArtifactStorage(sol.id).write(build_workspace_zip(source_bundle))

    async def _fake_bundle_for(self, *_args, **_kwargs):  # noqa: ANN001
        live_bundle = _bundle()
        live_bundle.python_files["workflows/main.py"] = "def run():\n    return 'live-db'\n"
        live_bundle.config_values = {"API_KEY": "secret-value"}
        live_bundle.table_data = {"things": [{"title": "row"}]}
        return live_bundle

    monkeypatch.setattr(SolutionCaptureService, "bundle_for", _fake_bundle_for)

    ctx, user = _admin(db_session)
    response = await export_solution(
        sol.id,
        ctx,
        user,
        mode="full",
        include_data=True,
        password="pw",
    )

    with zipfile.ZipFile(io.BytesIO(_response_bytes(response))) as z:
        assert z.read("workflows/main.py").decode() == "def run():\n    return 'artifact'\n"
        blob = z.read(".bifrost/secrets.enc").decode()
    content = decode_secrets_blob(blob, password="pw")
    assert content.config_values == {"API_KEY": "secret-value"}
    assert content.table_data == {"things": [{"title": "row"}]}
