"""Unit tests for `_sync_app_yaml_dependencies` — the post-push step that
mirrors `apps/<slug>/app.yaml::dependencies` into the Application model
column the validator/bundler actually read.

Regression context: pushing app.yaml via `bifrost sync` / `bifrost watch`
updates the YAML file in S3 but leaves Application.dependencies stale, so
the validator continues to flag newly-imported packages as missing
dependencies even though the user updated the YAML.
"""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from bifrost.cli import _sync_app_yaml_dependencies


def _mock_response(status_code: int, json_payload: dict | None = None) -> MagicMock:
    """Build a stand-in for httpx.Response with status_code + .json()."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload or {}
    return resp


def _write_app_yaml(tmp: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp / "app.yaml"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_changed_deps_get_pushed(tmp_path: pathlib.Path):
    """Local YAML deps differ from server → PUT is issued and returns True."""
    yaml_path = _write_app_yaml(
        tmp_path,
        'name: My App\ndependencies:\n  lodash: "4.17.21"\n  axios: "1.6.0"\n',
    )

    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(200, {
        "id": "11111111-1111-1111-1111-111111111111",
        "dependencies": {"lodash": "4.17.21"},  # missing axios
    }))
    client.put = AsyncMock(return_value=_mock_response(200, {}))

    result = await _sync_app_yaml_dependencies(client, "my-app", yaml_path)

    assert result is True
    client.get.assert_awaited_once_with("/api/applications/my-app")
    client.put.assert_awaited_once()
    put_args, put_kwargs = client.put.call_args
    assert put_args[0] == "/api/applications/11111111-1111-1111-1111-111111111111/dependencies"
    assert put_kwargs["json"] == {"lodash": "4.17.21", "axios": "1.6.0"}


@pytest.mark.asyncio
async def test_unchanged_deps_skip_put(tmp_path: pathlib.Path):
    """Local YAML deps match server exactly → no PUT, returns False."""
    yaml_path = _write_app_yaml(
        tmp_path,
        'name: My App\ndependencies:\n  lodash: "4.17.21"\n',
    )

    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(200, {
        "id": "abc",
        "dependencies": {"lodash": "4.17.21"},
    }))
    client.put = AsyncMock()

    result = await _sync_app_yaml_dependencies(client, "my-app", yaml_path)

    assert result is False
    client.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_deps_block_is_noop(tmp_path: pathlib.Path):
    """app.yaml without a `dependencies:` block → no-op, no GET, no PUT."""
    yaml_path = _write_app_yaml(tmp_path, 'name: Bare App\n')

    client = MagicMock()
    client.get = AsyncMock()
    client.put = AsyncMock()

    result = await _sync_app_yaml_dependencies(client, "bare-app", yaml_path)

    assert result is False
    client.get.assert_not_awaited()
    client.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_app_404_is_noop(tmp_path: pathlib.Path):
    """App doesn't exist yet (e.g. mid create+push flow) → graceful no-op."""
    yaml_path = _write_app_yaml(
        tmp_path,
        'dependencies:\n  lodash: "4.17.21"\n',
    )

    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(404))
    client.put = AsyncMock()

    result = await _sync_app_yaml_dependencies(client, "missing-app", yaml_path)

    assert result is False
    client.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_local_yaml_is_noop(tmp_path: pathlib.Path):
    """Local file deleted between push detection and helper call → no-op."""
    yaml_path = tmp_path / "does-not-exist.yaml"

    client = MagicMock()
    client.get = AsyncMock()
    client.put = AsyncMock()

    result = await _sync_app_yaml_dependencies(client, "ghost", yaml_path)

    assert result is False
    client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_dep_values_are_coerced_to_strings(tmp_path: pathlib.Path):
    """YAML may parse '0.469' as a float — the dependencies endpoint requires
    str values, so coerce before PUT.
    """
    yaml_path = _write_app_yaml(
        tmp_path,
        'dependencies:\n  lucide-react: 0.469\n  some-pkg: 1\n',
    )

    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(200, {
        "id": "abc",
        "dependencies": {},
    }))
    client.put = AsyncMock(return_value=_mock_response(200, {}))

    result = await _sync_app_yaml_dependencies(client, "my-app", yaml_path)

    assert result is True
    put_kwargs = client.put.call_args.kwargs
    for v in put_kwargs["json"].values():
        assert isinstance(v, str)


@pytest.mark.asyncio
async def test_put_failure_raises(tmp_path: pathlib.Path):
    """Non-200 PUT surfaces as an exception so the caller logs once."""
    yaml_path = _write_app_yaml(
        tmp_path,
        'dependencies:\n  lodash: "4.17.21"\n',
    )

    client = MagicMock()
    client.get = AsyncMock(return_value=_mock_response(200, {
        "id": "abc",
        "dependencies": {},
    }))
    client.put = AsyncMock(return_value=_mock_response(500))

    with pytest.raises(RuntimeError, match="PUT HTTP 500"):
        await _sync_app_yaml_dependencies(client, "my-app", yaml_path)
