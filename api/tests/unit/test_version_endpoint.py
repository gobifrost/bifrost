"""The /api/version endpoint reports the CLI floor and legacy bridge.

New CLIs enforce ``min_cli_version``. ``contract_version`` remains exposed as a
one-release bridge so already-installed older CLIs upgrade into that policy.
"""

from __future__ import annotations

import pytest

from shared.contract_version import CONTRACT_VERSION
from shared.version import MIN_CLI_VERSION


@pytest.mark.asyncio
async def test_version_endpoint_reports_contract_version() -> None:
    from src.routers.version import get_version_info

    result = await get_version_info()

    assert result.contract_version == CONTRACT_VERSION
    assert result.min_cli_version == MIN_CLI_VERSION
    # The build version string is still present (unchanged behavior).
    assert isinstance(result.version, str)
