"""The CLI and SDK open the same explicit device approval flow."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bifrost._device_auth import device_login_prompt


def test_prompt_shows_code_and_opens_complete_url(capsys):
    url = device_login_prompt("https://example.test/", "/device", "ABCD-EFGH")
    assert url == "https://example.test/device?user_code=ABCD-EFGH"
    output = capsys.readouterr().out
    assert f"Open: {url}" in output
    assert "Code: ABCD-EFGH" in output
    assert "Check that the browser shows this code, then choose Connect CLI." in output
    assert "Only approve a login you started." in output


def test_prompt_preserves_query_and_replaces_existing_code():
    url = device_login_prompt(
        "https://example.test", "/device?theme=dark&user_code=OLD#connect", "ABCD-EFGH",
    )
    assert url == "https://example.test/device?theme=dark&user_code=ABCD-EFGH#connect"


@pytest.mark.parametrize("module_name", ["bifrost.cli", "bifrost.client"])
def test_login_opens_code_url_and_denial_never_saves_credentials(monkeypatch, module_name):
    module = importlib.import_module(module_name)
    client = AsyncMock()
    client.post.side_effect = [
        SimpleNamespace(status_code=200, json=lambda: {
            "device_code": "device-code", "user_code": "ABCD-EFGH",
            "verification_url": "/device", "expires_in": 300, "interval": 5,
        }),
        SimpleNamespace(status_code=200, json=lambda: {"error": "access_denied"}),
    ]
    client.__aenter__.return_value = client
    monkeypatch.setattr(module.httpx, "AsyncClient", MagicMock(return_value=client))
    browser = MagicMock()
    monkeypatch.setattr(module.webbrowser, "open", browser)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    save = MagicMock()
    if module_name == "bifrost.cli":
        monkeypatch.setattr(module.credentials, "save_credentials", save)
    else:
        monkeypatch.setattr(module, "save_credentials", save)

    assert asyncio.run(module.login_flow("https://example.test")) is False
    browser.assert_called_once_with("https://example.test/device?user_code=ABCD-EFGH")
    save.assert_not_called()
