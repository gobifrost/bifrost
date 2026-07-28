from __future__ import annotations

import httpx
import pytest

from bifrost.cli import _api_request


class _TimeoutClient:
    async def post(self, _path: str, **_kwargs):
        raise httpx.ReadTimeout("")


@pytest.mark.asyncio
async def test_generic_api_timeout_is_never_a_blank_error(capsys) -> None:
    exit_code = await _api_request(
        "POST",
        "/api/applications/app-id/publish",
        None,
        client=_TimeoutClient(),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "timed out after 30 seconds" in captured.err
    assert captured.err.strip() != "Error:"
