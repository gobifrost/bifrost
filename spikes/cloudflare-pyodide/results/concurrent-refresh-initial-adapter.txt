"""Replace only the SDK's synchronous helper-thread bridge in fresh Pyodide.

Upstream async refresh owns coordination, credential generation, persistence,
redaction and API authorization. This is not a refresh/token implementation.
"""


def refresh_sync(api_url: str, observed_access_token: str) -> str | None:
    from pyodide.ffi import run_sync
    from bifrost import client

    token = run_sync(client.refresh_connection_access_token(api_url, observed_access_token))
    client._register_refreshed(token)
    return token


def install():
    from bifrost import client

    client._refresh_connection_access_token_sync = refresh_sync
