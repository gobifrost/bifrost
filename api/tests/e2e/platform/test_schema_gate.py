"""Schema gate against the migrated test database."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.schema_gate import current_revisions, expected_heads, wait_for_schema

_API_DIR = Path(__file__).resolve().parents[3]


def test_expected_heads_match_alembic_heads() -> None:
    result = subprocess.run(
        ["alembic", "heads"],
        cwd=_API_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    cli_heads = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}

    assert len(cli_heads) == 1
    assert expected_heads() == cli_heads


@pytest.mark.asyncio
async def test_migrated_database_passes_gate_immediately(
    async_engine: AsyncEngine,
) -> None:
    assert await current_revisions(async_engine) == expected_heads()

    await wait_for_schema(async_engine, max_wait=0)
