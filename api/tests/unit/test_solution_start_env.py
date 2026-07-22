"""The `solution start` Vite child must ride the LOCAL PROXY, not the upstream.

The proxy is where install scope gets injected (?solution=, auth, app header).
Pointing the app bundle's BIFROST_API_URL at the upstream API bypasses that
injection entirely: local workflow edits silently don't run locally, the
install's own tables 404, and declared-location file writes 403 (drive
finding, 2026-07-02). The browser must address that proxy through its own
successful origin, not an internal loopback URL that an outer Codex/VPN proxy
may have remapped to a different browser-visible port.
"""
from bifrost.commands import solution as solution_cmd
from bifrost.commands.solution import _scaffold_api_url, _vite_child_env


class TestScaffoldApiUrl:
    """scaffold-app must never bake the hardcoded localhost:8000 fallback when
    a real URL is knowable — explicit flag > env > the authenticated client."""

    def test_explicit_flag_wins(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://env:1")
        assert _scaffold_api_url("http://flag:2") == "http://flag:2"

    def test_env_wins_over_client(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://env:1")
        monkeypatch.setattr(
            solution_cmd.BifrostClient,
            "get_instance",
            staticmethod(lambda require_auth=True: type("C", (), {"api_url": "http://client:3"})()),
        )
        assert _scaffold_api_url(None) == "http://env:1"

    def test_logged_in_client_beats_hardcoded_fallback(self, monkeypatch):
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.setattr(
            solution_cmd.BifrostClient,
            "get_instance",
            staticmethod(lambda require_auth=True: type("C", (), {"api_url": "http://client:3"})()),
        )
        assert _scaffold_api_url(None) == "http://client:3"

    def test_hardcoded_fallback_only_when_logged_out(self, monkeypatch):
        monkeypatch.delenv("BIFROST_API_URL", raising=False)

        def _not_logged_in(require_auth=True):
            raise RuntimeError("Not logged in")

        monkeypatch.setattr(
            solution_cmd.BifrostClient, "get_instance", staticmethod(_not_logged_in)
        )
        assert _scaffold_api_url(None) == "http://localhost:8000"


def test_vite_child_env_points_bundle_at_browser_same_origin():
    env = _vite_child_env(
        {"PATH": "/usr/bin", "BIFROST_API_URL": "http://upstream:34173"},
        app_id="2a9d06da-cc86-49ff-b3b5-26748c31f73e",
        org_id="org-1",
        access_token="tok",
    )
    # `/` tells BifrostProvider to use the browser's current origin. If Codex
    # exposes localhost:3777 as localhost:62464, API + websocket traffic must
    # stay on :62464 so the outer forwarder can carry it to the local proxy.
    assert env["BIFROST_API_URL"] == "/"
    assert env["VITE_BIFROST_APP_ID"] == "2a9d06da-cc86-49ff-b3b5-26748c31f73e"
    assert env["VITE_BIFROST_ORG_ID"] == "org-1"
    assert env["BIFROST_ACCESS_TOKEN"] == "tok"
    # Base env is inherited, not replaced.
    assert env["PATH"] == "/usr/bin"


def test_vite_child_env_omits_org_var_for_global_installs():
    """A global install has NO org — the app must see orgScope null, not "".

    Setting VITE_BIFROST_ORG_ID="" flowed an empty-string orgScope into
    BifrostProvider (`?? null` doesn't catch ""), diverging from the proxy
    config's None for the same install (issue #463).
    """
    env = _vite_child_env(
        {"PATH": "/usr/bin"},
        app_id="2a9d06da-cc86-49ff-b3b5-26748c31f73e",
        org_id=None,
        access_token="tok",
    )
    assert "VITE_BIFROST_ORG_ID" not in env


async def test_solution_proxy_uses_active_client_refresh_authority():
    class Client:
        api_url = "http://api.example"
        _access_token = "stale-token"

        def __init__(self):
            self.observed = None

        async def refresh_access_token(self, observed_access_token):
            self.observed = observed_access_token
            self._access_token = "fresh-token"
            return self._access_token

    client = Client()
    chosen = type("Chosen", (), {"app_id": "app-id"})()
    cfg = solution_cmd._dev_proxy_config(
        client,
        chosen,
        {"id": "org-id"},
        "solution-id",
        False,
    )

    assert cfg.refresh_token is not None
    token = await cfg.refresh_token("stale-token")

    assert client.observed == "stale-token"
    assert token == client._access_token == "fresh-token"
