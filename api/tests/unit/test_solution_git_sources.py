"""Solution source selection is explicit, scoped, read-only and fail-closed."""
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared import solution_git_sources as sources


@pytest.fixture
def connection():
    scope = uuid4()
    row = SimpleNamespace(id=uuid4(), organization_id=scope)
    repository = {"id": 7, "full_name": "acme/private-solution", "default_branch": "main", "private": True}
    payload = {"app": {"id": 42, "name": "Solution App", "private_key": "private-key", "client_secret": "client-secret"},
               "installation_id": 15, "repositories": [repository], "enabled": True, "generation": "generation-1"}
    return row, payload


@pytest.mark.parametrize("url", [
    "https://user:secret@github.com/acme/repo", "http://github.com/acme/repo",
    "https://github.com.evil.test/acme/repo", "https://evil.test/acme/repo",
    "https://github.com/acme/repo?next=evil", "https://github.com/acme/repo#part",
    "https://github.com/acme/../other", "https://github.com/acme/%2e%2e",
    "https://github.com/acme/..", "file:///private/repo", "git@github.com:acme/repo.git",
])
@pytest.mark.asyncio
async def test_invalid_private_source_url_is_rejected_before_credentials(monkeypatch, url):
    load = AsyncMock()
    monkeypatch.setattr(sources, "load_source", load)
    with pytest.raises(HTTPException):
        await sources.resolve_credentials(None, uuid4(), uuid4(), url)
    load.assert_not_awaited()


@pytest.mark.parametrize("url", ["https://github.com/acme/private-solution", "https://github.com/ACME/private-solution.git"])
@pytest.mark.asyncio
async def test_selected_source_uses_exact_allowed_repository_and_read_permission(monkeypatch, connection, url):
    row, payload = connection
    load = AsyncMock(return_value=connection)
    monkeypatch.setattr(sources, "load_source", load)
    credentials = await sources.resolve_credentials(None, row.id, row.organization_id, url)
    load.assert_awaited_once_with(None, row.id, row.organization_id)
    assert credentials.contents == "read"
    assert credentials.config.app["repository_id"] == 7
    assert credentials.config.app["workflows"] is False
    assert credentials.repo_url == "https://github.com/acme/private-solution"
    assert "private-key" not in repr(credentials.config)


@pytest.mark.asyncio
async def test_another_repository_cannot_borrow_a_source(monkeypatch, connection):
    row, _ = connection
    monkeypatch.setattr(sources, "load_source", AsyncMock(return_value=connection))
    with pytest.raises(HTTPException) as error:
        await sources.resolve_credentials(None, row.id, row.organization_id, "https://github.com/acme/other")
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_changed_connection_fences_existing_credentials(monkeypatch, connection):
    row, payload = connection
    credentials = sources.SolutionGitCredentials(None, row, payload, payload["repositories"][0])
    monkeypatch.setattr(sources, "load_source", AsyncMock(return_value=(row, {**payload, "generation": "generation-2"})))
    with pytest.raises(HTTPException) as error:
        await credentials._current()
    assert error.value.status_code == 409


@pytest.mark.parametrize("state", ["suspended", "permission_removed", "repository_removed", "repository_renamed"])
@pytest.mark.asyncio
async def test_live_installation_and_repository_access_are_verified(monkeypatch, connection, state):
    row, payload = connection
    credentials = sources.SolutionGitCredentials(None, row, payload, payload["repositories"][0])
    credentials.get_token = AsyncMock(return_value="installation-token")
    credentials._current = AsyncMock()
    monkeypatch.setattr(sources, "app_jwt", lambda _: "app-assertion")
    installation = {"permissions": {"contents": "read"}}
    if state == "suspended":
        installation["suspended_at"] = "now"
    if state == "permission_removed":
        installation["permissions"]["contents"] = "none"
    monkeypatch.setattr(sources, "_request", AsyncMock(return_value=installation))
    repos = [] if state == "repository_removed" else [{"id": 7, "full_name": "acme/renamed"}]
    monkeypatch.setattr(sources, "_pages", AsyncMock(return_value=repos))
    with pytest.raises(HTTPException) as error:
        await credentials.verify()
    assert error.value.status_code == 403
    if state in {"suspended", "permission_removed"}:
        credentials.get_token.assert_not_awaited()


@pytest.fixture
def private_transport(monkeypatch):
    credentials = SimpleNamespace(repo_url="https://github.com/acme/private-solution",
        verify=AsyncMock(return_value="secret-installation-token"), _current=AsyncMock())
    monkeypatch.setattr(sources, "resolve_credentials", AsyncMock(return_value=credentials))
    clone = Mock(return_value=SimpleNamespace(close=Mock()))
    monkeypatch.setattr("git.Repo.clone_from", clone)
    return credentials, clone


@pytest.mark.asyncio
async def test_private_clone_keeps_credentials_out_of_urls_and_disables_redirects(private_transport, tmp_path):
    credentials, clone = private_transport
    await sources.clone_authenticated(None, uuid4(), uuid4(), credentials.repo_url, tmp_path, "v1.0.0")
    args, options = clone.call_args
    assert args == (credentials.repo_url, str(tmp_path))
    assert options["branch"] == "v1.0.0"
    env = options["env"]
    encoded = base64.b64encode(b"x-access-token:secret-installation-token").decode()
    assert env["GIT_CONFIG_VALUE_0"] == "Authorization: Basic " + encoded
    assert env["GIT_CONFIG_KEY_2"] == "http.followRedirects"
    assert env["GIT_CONFIG_VALUE_2"] == "false"
    credentials._current.assert_awaited_once()


@pytest.mark.asyncio
async def test_git_failure_does_not_echo_transport_secrets(private_transport, tmp_path):
    credentials, clone = private_transport
    clone.side_effect = RuntimeError("secret-installation-token plus private transport details")
    with pytest.raises(HTTPException) as error:
        await sources.clone_authenticated(None, uuid4(), uuid4(), credentials.repo_url, tmp_path, None)
    assert "secret-installation-token" not in error.value.detail
    assert "private transport details" not in error.value.detail
    assert error.value.__suppress_context__


@pytest.mark.asyncio
async def test_disabled_source_never_starts_a_clone(private_transport, tmp_path):
    credentials, clone = private_transport
    credentials.verify.side_effect = HTTPException(409, "disconnected")
    with pytest.raises(HTTPException):
        await sources.clone_authenticated(None, uuid4(), uuid4(), credentials.repo_url, tmp_path, None)
    clone.assert_not_called()


@pytest.mark.asyncio
async def test_connection_change_during_fetch_is_rejected(private_transport, tmp_path):
    credentials, _ = private_transport
    credentials._current.side_effect = HTTPException(409, "source changed")
    with pytest.raises(HTTPException) as error:
        await sources.clone_authenticated(None, uuid4(), uuid4(), credentials.repo_url, tmp_path, None)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_public_selection_uses_the_existing_clone_path(monkeypatch, tmp_path):
    public = AsyncMock()
    private = AsyncMock()
    monkeypatch.setattr("src.services.solutions.git_sync.clone_repo_to_dir", public)
    monkeypatch.setattr(sources, "clone_authenticated", private)
    await sources.clone_solution_repository(None, None, None, "https://github.com/acme/public", tmp_path, None)
    public.assert_awaited_once_with("https://github.com/acme/public", tmp_path, ref=None)
    private.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_private_source_never_falls_back_to_public(monkeypatch, tmp_path):
    public = AsyncMock()
    monkeypatch.setattr("src.services.solutions.git_sync.clone_repo_to_dir", public)
    monkeypatch.setattr(sources, "clone_authenticated", AsyncMock(side_effect=HTTPException(403, "denied")))
    with pytest.raises(HTTPException):
        await sources.clone_solution_repository(AsyncMock(), uuid4(), uuid4(), "https://github.com/acme/private", tmp_path, None)
    public.assert_not_awaited()


def test_encrypted_source_payload_is_not_an_api_shape(connection):
    row, payload = connection
    result = sources._public(row, payload).model_dump_json()
    assert "private-key" not in result and "client-secret" not in result
    assert "private_key" not in result and "client_secret" not in result
