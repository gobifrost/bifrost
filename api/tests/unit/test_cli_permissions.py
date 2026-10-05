"""Tests for ``bifrost permissions list``.

The tests mock ``BifrostClient.get_instance`` so no network or credentials are
required (mirrors ``test_cli_services.py``).
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest.mock as mock

import httpx
from click.testing import CliRunner

# Ensure the standalone bifrost package is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.permissions import permissions_group  # noqa: E402

_CATALOG = [
    {
        "domain": "roles",
        "title": "Roles",
        "area": "Identity & access",
        "description": "Role definitions.",
        "who_should_hold": "Platform admins.",
        "actions": ["read", "readwrite"],
        "privileged": ["roles.readwrite"],
        "scope": "platform_wide",
        "enforced": True,
    },
    {
        "domain": "tables",
        "title": "Tables",
        "area": "Data & content",
        "description": "Table definitions.",
        "who_should_hold": "Platform admins.",
        "actions": ["read", "readwrite"],
        "privileged": [],
        "scope": "per_organization",
        "enforced": False,
    },
    {
        "domain": "claims",
        "title": "Claims",
        "area": "Data & content",
        "description": "Claim records.",
        "who_should_hold": "Platform admins.",
        "actions": ["read"],
        "privileged": [],
        "scope": "varies",
        "enforced": False,
    },
]


def _invoke(args: list[str], captured: dict) -> "CliRunner._Result":  # type: ignore[name-defined]
    async def get(path, params=None):  # type: ignore[no-untyped-def]
        captured["get_path"] = path
        return httpx.Response(
            200,
            json=_CATALOG,
            request=httpx.Request("GET", "https://bifrost.test/api/permissions/catalog"),
        )

    client = mock.AsyncMock()
    client.get = get
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        return CliRunner().invoke(permissions_group, args)


def test_list_reads_the_catalog_endpoint() -> None:
    captured: dict = {}
    result = _invoke(["list"], captured)
    assert result.exit_code == 0, result.output
    assert captured["get_path"] == "/api/permissions/catalog"


def test_list_prints_a_table_of_area_permission_scope_enforced() -> None:
    result = _invoke(["list"], {})
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "AREA               PERMISSION  SCOPE          ENFORCED",
        "Identity & access  roles       platform-wide  yes",
        "Data & content     tables      per-org        no",
        "Data & content     claims      varies         no",
    ]


def test_list_json_prints_the_raw_catalog() -> None:
    result = _invoke(["list", "--json"], {})
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _CATALOG
