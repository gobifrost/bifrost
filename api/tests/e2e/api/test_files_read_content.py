"""Content-decoding behavior of ``POST /api/files/read``.

Replaces ``tests/unit/test_mcp_tools_file_index.py``, which tested a private
``_read_from_s3`` helper in the MCP code_editor module. Commit 3b3f9a868 made
workspace file operations canonical: the MCP tool became a thin HTTP wrapper
(``bifrost_read_file``) and the S3 read moved behind this endpoint, so the
helper — and its test — no longer had a subject.

The three behaviors that test covered still matter, so they are asserted here
against the endpoint that now owns them:

* text content round-trips as decoded UTF-8   (was: returns decoded content)
* a missing path is a 404                     (was: returns None)
* binary content is a 400 telling the caller  (was: returns None silently)
  to re-request with ``binary=true``

The binary case is a deliberate behavior change, not a port: swallowing a
UnicodeDecodeError as "not found" made a readable file look absent. The
endpoint now distinguishes the two, and this pins that.
"""
import base64

import pytest

pytestmark = pytest.mark.e2e


def _write(e2e_client, headers, path, content, *, binary=False):
    return e2e_client.post("/api/files/write", headers=headers, json={
        "path": path,
        "content": content,
        "mode": "cloud",
        "location": "workspace",
        "binary": binary,
    })


def _read(e2e_client, headers, path, *, binary=False):
    return e2e_client.post("/api/files/read", headers=headers, json={
        "path": path,
        "mode": "cloud",
        "location": "workspace",
        "binary": binary,
    })


def _delete(e2e_client, headers, path):
    return e2e_client.post("/api/files/delete", headers=headers, json={
        "path": path,
        "mode": "cloud",
        "location": "workspace",
    })


def test_read_returns_decoded_text_content(e2e_client, platform_admin):
    """A UTF-8 text file reads back as decoded content."""
    path = "modules/_read_probe_text.py"
    source = "def hello(): pass\n"
    assert _write(
        e2e_client, platform_admin.headers, path, source
    ).status_code == 204

    try:
        resp = _read(e2e_client, platform_admin.headers, path)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content"] == source
        assert body["binary"] is False
    finally:
        _delete(e2e_client, platform_admin.headers, path)


def test_read_missing_path_is_404(e2e_client, platform_admin):
    """A path with no stored file is a 404, not an empty 200."""
    resp = _read(
        e2e_client, platform_admin.headers, "modules/_read_probe_absent.py"
    )
    assert resp.status_code == 404, resp.text


def test_read_binary_content_as_text_is_400(e2e_client, platform_admin):
    """Undecodable bytes are a 400 that names the fix, not a silent miss."""
    path = "modules/_read_probe_binary.bin"
    payload = base64.b64encode(b"\x80\x81\x82\xff").decode()
    assert _write(
        e2e_client, platform_admin.headers, path, payload, binary=True
    ).status_code == 204

    try:
        resp = _read(e2e_client, platform_admin.headers, path)
        assert resp.status_code == 400, (
            f"binary read should be a 400, got {resp.status_code}: {resp.text}"
        )
        assert "binary" in resp.text.lower()

        # And the documented remedy actually works.
        as_binary = _read(e2e_client, platform_admin.headers, path, binary=True)
        assert as_binary.status_code == 200, as_binary.text
        body = as_binary.json()
        assert body["binary"] is True
        assert base64.b64decode(body["content"]) == b"\x80\x81\x82\xff"
    finally:
        _delete(e2e_client, platform_admin.headers, path)
