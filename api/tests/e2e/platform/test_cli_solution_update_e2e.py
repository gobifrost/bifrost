"""E2E: `bifrost solution update` edits install-local fields against a live API.

Proves the CLI wires through to ``PATCH /api/solutions/{id}``: a workspace
descriptor selects the install, the passed flags change the stored access gates,
and an omitted field (name) is left untouched.
"""
from __future__ import annotations

import uuid

import pytest

from bifrost.commands.solution import solution_group

pytestmark = pytest.mark.e2e


def _invoke_cli(group, args: list):
    from click.testing import CliRunner

    return CliRunner().invoke(group, args, standalone_mode=False, catch_exceptions=False)


def test_solution_update_cli_sets_access_gates(
    e2e_client, platform_admin, cli_client, e2e_api_url, tmp_path
) -> None:
    headers = {"Authorization": f"Bearer {platform_admin.access_token}"}
    slug = f"cli-update-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions", headers=headers, json={"slug": slug, "name": slug.upper()}
    )
    assert created.status_code in (200, 201), created.text
    sol = created.json()

    (tmp_path / "bifrost.solution.yaml").write_text(f"slug: {slug}\nname: Ignored\n")

    result = _invoke_cli(
        solution_group,
        [
            "update", str(tmp_path), "--solution", sol["id"], "--url", e2e_api_url,
            "--allow-outbound-access", "--no-allow-inbound-access",
        ],
    )
    assert result.exit_code == 0, result.output

    body = e2e_client.get(f"/api/solutions/{sol['id']}", headers=headers).json()
    assert body["allow_outbound_access"] is True
    assert body["allow_inbound_access"] is False
    assert body["name"] == slug.upper()
