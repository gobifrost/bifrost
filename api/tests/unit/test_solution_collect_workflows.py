"""CLI `_collect_workflows` — reads .bifrost/workflows.yaml (keyed by UUID) into
the deploy bundle. The deployer's `_upsert_workflows` consumes the full metadata
set (endpoint_enabled, public_endpoint, timeout_seconds, category, tags), so the
CLI collector must pass them through — otherwise a disconnected redeploy silently
resets an exported workflow's endpoint/timeout to defaults (Codex P2-e)."""
from __future__ import annotations

import pathlib
import sys

import click
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.solution import _collect_workflows  # noqa: E402


def _ws(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text(body)
    return tmp_path


def test_collect_workflows_preserves_full_metadata(tmp_path) -> None:
    ws = _ws(
        tmp_path,
        "workflows:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    id: 11111111-1111-1111-1111-111111111111\n"
        "    name: Sync Tickets\n"
        "    function_name: sync_tickets\n"
        "    path: workflows/sync.py\n"
        "    type: workflow\n"
        "    description: Pulls tickets\n"
        "    access_level: organization\n"
        "    endpoint_enabled: true\n"
        "    allowed_methods: [GET, POST]\n"
        "    public_endpoint: true\n"
        "    timeout_seconds: 600\n"
        "    category: Tickets\n"
        "    tags: [psa, sync]\n",
    )
    wfs = _collect_workflows(ws)
    assert len(wfs) == 1
    w = wfs[0]
    assert w["id"] == "11111111-1111-1111-1111-111111111111"
    assert w["name"] == "Sync Tickets"
    assert w["function_name"] == "sync_tickets"
    assert w["path"] == "workflows/sync.py"
    assert w["type"] == "workflow"
    assert w["description"] == "Pulls tickets"
    assert w["access_level"] == "organization"
    # These five are what a narrowed collector silently dropped (P2-e).
    assert w["endpoint_enabled"] is True
    assert w["allowed_methods"] == ["GET", "POST"]
    assert w["public_endpoint"] is True
    assert w["timeout_seconds"] == 600
    assert w["category"] == "Tickets"
    assert w["tags"] == ["psa", "sync"]


def test_collect_workflows_empty_when_no_manifest(tmp_path) -> None:
    assert _collect_workflows(tmp_path) == []


@pytest.mark.parametrize("wf_path", ["../outside.py", "/tmp/outside.py"])
def test_collect_workflows_rejects_manifest_path_outside_workspace(
    tmp_path: pathlib.Path,
    wf_path: str,
) -> None:
    ws = _ws(
        tmp_path,
        "workflows:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    name: Escape\n"
        "    function_name: run\n"
        f"    path: {wf_path}\n",
    )

    with pytest.raises(click.ClickException, match="escapes the workspace"):
        _collect_workflows(ws)
