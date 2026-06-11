"""The /api/version endpoint must report the server's contract_version.

The CLI's contract gate reads ``contract_version`` from this response to decide
compatibility, so the endpoint must surface it and it must equal the server-side
source of truth.
"""

from __future__ import annotations

import pytest

from shared.contract_version import CONTRACT_VERSION


@pytest.mark.asyncio
async def test_version_endpoint_reports_contract_version() -> None:
    from src.routers.version import get_version_info

    result = await get_version_info()

    assert result.contract_version == CONTRACT_VERSION
    # The build version string is still present (unchanged behavior).
    assert isinstance(result.version, str)
