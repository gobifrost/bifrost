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

from bifrost.commands.permissions import permission_names, permissions_group  # noqa: E402

_CATALOG = [
    {
        "domain": "roles",
        "title": "Roles",
        "area": "Identity & Access",
        "description": "Role definitions.",
        "who_should_hold": "Platform admins.",
        "actions": ["read", "readwrite"],
        "names": {"roles.read": "Read Roles", "roles.readwrite": "Read and Write Roles"},
        "privileged": ["roles.readwrite"],
        "scope": "platform_wide",
        "enforced": True,
    },
    {
        "domain": "tables",
        "title": "Tables",
        "area": "Data & Content",
        "description": "Table definitions.",
        "who_should_hold": "Platform admins.",
        "actions": ["read", "readwrite"],
        "names": {
            "tables.read": "Read Tables",
            "tables.readwrite": "Read and Write Tables",
        },
        "privileged": [],
        "scope": "per_organization",
        "enforced": False,
    },
    {
        "domain": "claims",
        "title": "Claims",
        "area": "Data & Content",
        "description": "Claim records.",
        "who_should_hold": "Platform admins.",
        "actions": ["read"],
        "names": {"claims.read": "Read Claims", "claims.readwrite": "Read and Write Claims"},
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


def test_list_prints_one_row_per_checked_permission_with_its_name() -> None:
    # claims.readwrite is named but no route checks it, so it is not listed.
    result = _invoke(["list"], {})
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "AREA               NAME                   PERMISSION        SCOPE          ENFORCED",
        "Identity & Access  Read Roles             roles.read        platform-wide  yes",
        "Identity & Access  Read and Write Roles   roles.readwrite   platform-wide  yes",
        "Data & Content     Read Tables            tables.read       per-org        no",
        "Data & Content     Read and Write Tables  tables.readwrite  per-org        no",
        "Data & Content     Read Claims            claims.read       varies         no",
    ]


def test_names_cover_the_catalog_and_the_wildcard() -> None:
    names = permission_names(_CATALOG)
    assert names["tables.readwrite"] == "Read and Write Tables"
    assert names["*"] == "All Permissions"


def test_list_json_prints_the_raw_catalog() -> None:
    result = _invoke(["list", "--json"], {})
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _CATALOG
