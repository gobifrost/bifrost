"""E2E: install blocks on an unmet module dependency (Task 8).

A bundle whose workflow imports ``modules.absent`` but ships NO
``modules/absent.py`` must be REFUSED at install with 422 naming the missing
module — and nothing must land (mirrors the "wrong password lands nothing"
discipline). The block runs BEFORE any DB/S3 write, so a subsequent listing
shows the install never appeared.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.e2e


def _make_zip(slug: str, *, workflow_src: str, modules: dict[str, str] | None = None) -> bytes:
    """A minimal Solution workspace zip: descriptor + one workflow whose source
    is ``workflow_src``, plus any ``modules/<name>.py`` files in ``modules``."""
    wf_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/workflows/main"))
    descriptor = f"slug: {slug}\nname: {slug.upper()}\nscope: global\n"
    workflows_yaml = (
        "workflows:\n"
        f"  {wf_id}:\n"
        f"    id: {wf_id}\n"
        "    name: main\n"
        "    function_name: run\n"
        "    path: workflows/main.py\n"
    )
    files = {
        "bifrost.solution.yaml": descriptor,
        ".bifrost/workflows.yaml": workflows_yaml,
        "workflows/main.py": workflow_src,
    }
    for rel, src in (modules or {}).items():
        files[f"modules/{rel}"] = src
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


async def test_install_blocks_on_missing_module(e2e_client, platform_admin):
    headers = platform_admin.headers
    upload_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    slug = f"zip-missingmod-{uuid.uuid4().hex[:8]}"

    # workflow imports modules.absent, but no modules/absent.py is shipped.
    data = _make_zip(
        slug,
        workflow_src="from modules.absent import x\n\n\ndef run(sdk):\n    return x\n",
    )

    inst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
        data={"config_values": "{}"},
    )
    assert inst.status_code == 422, inst.text
    assert "modules.absent" in inst.text, inst.text

    # Nothing landed: the install for this slug must not exist.
    listing = e2e_client.get("/api/solutions", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = [s for s in listing.json()["solutions"] if s["slug"] == slug]
    assert rows == [], "a blocked install must leave no Solution row"


async def test_install_succeeds_when_module_present(e2e_client, platform_admin):
    """The block must NOT false-positive: a bundle whose imported module IS
    shipped installs normally."""
    headers = platform_admin.headers
    upload_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    slug = f"zip-presentmod-{uuid.uuid4().hex[:8]}"

    data = _make_zip(
        slug,
        workflow_src="from modules.present import x\n\n\ndef run(sdk):\n    return x\n",
        modules={"present.py": "x = 1\n"},
    )

    inst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
        data={"config_values": "{}"},
    )
    assert inst.status_code in (200, 201), inst.text

    listing = e2e_client.get("/api/solutions", headers=headers)
    rows = [s for s in listing.json()["solutions"] if s["slug"] == slug]
    assert len(rows) == 1, "a bundle with all modules present must install"
