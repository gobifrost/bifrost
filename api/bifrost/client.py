"""
Bifrost SDK Client

HTTP client for Bifrost API communication.
Supports two modes:
1. Platform mode: Client is injected by workflow engine
2. CLI mode: Auto-initializes from credentials file stored by 'bifrost login'
"""

import asyncio
import logging
import sys
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .credentials import (
    clear_credentials,
    get_credentials,
    is_token_expired,
    load_dotenv_context,
    resolve_credentials,
    resolve_current_connection,
    resolve_environment_url,
    save_refreshed_credentials,
    save_credentials,
)

logger = logging.getLogger(__name__)

# Global client injection for platform mode
_injected_client: Optional["BifrostClient"] = None

# Trusted engine-local Unix-socket transport.
#
# The worker parent injects this path into each execution child over the
# fork command. It is never developer-set and never read from configuration.
# The client builds ordinary sync/async HTTPX clients against the socket and
# exposes them through the single :meth:`BifrostClient.engine_request` entry
# point; every other request keeps the network client. A local attempt never
# falls back to the network API.
_engine_socket_path: str | None = None

# Placeholder authority for engine-local requests. The Unix-socket transport
# ignores the host, but HTTPX needs an ``http`` base URL so it never attempts
# a TLS handshake on the socket (which an ``https`` base URL would).
_ENGINE_SOCKET_BASE_URL = "http://bifrost-engine"


class BifrostAPIError(httpx.HTTPStatusError):
    """An authenticated Bifrost API request returned a non-success status."""


class BifrostAuthenticationError(BifrostAPIError):
    """The active Bifrost session could not authenticate the request."""


class BifrostAuthorizationError(BifrostAPIError):
    """The active Bifrost identity may not perform the request."""


class BifrostDependencyError(BifrostAPIError):
    """A declared Bifrost dependency is unavailable."""

# Retry config for transient 5xx — see workstream E of issue #171.
# SDK is machine-to-machine, so the retry budget is more generous than
# the user-facing client.
TRANSIENT_5XX_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})
IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "PUT", "DELETE", "HEAD", "OPTIONS"})
SDK_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.5, 4.0, 10.0, 20.0)


# Transient transport-layer failures (connection reset, read/connect timeout,
# pool exhaustion, protocol error). Unlike a 5xx these RAISE rather than return a
# response, so they bypassed the retry loop entirely: one reset worker<->api
# connection failed the whole workflow. Retried under the same gate as 5xx:
# idempotent methods, or a caller that opts in with ``retry_transient=True`` for
# an operation that is idempotent in effect (an ON CONFLICT upsert, an
# absolute-value cursor write, etc.).
TRANSIENT_TRANSPORT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _is_idempotent(method: str) -> bool:
    return method.upper() in IDEMPOTENT_METHODS


def _is_transient_5xx(status_code: int) -> bool:
    return status_code in TRANSIENT_5XX_STATUS_CODES


async def _send_with_retry(
    method: str,
    do_send: Callable[[], Awaitable[httpx.Response]],
    *,
    retry_transient: bool = False,
) -> httpx.Response:
    """Retry transient 5xx AND transient transport errors on retryable requests.

    A request is retryable when the method is idempotent OR the caller passes
    ``retry_transient=True`` (asserting the operation is idempotent-in-effect).
    Non-retryable requests make a single attempt, preserving plain POST/PATCH
    behaviour.

    The do_send callable must be safe to invoke multiple times; it should
    re-issue the request fresh each call (httpx Request objects with bodies
    are single-use, so callers either pass simple methods or rebuild the
    request inside the closure).
    """
    retryable = _is_idempotent(method) or retry_transient
    backoffs = SDK_RETRY_BACKOFF_SECONDS if retryable else ()
    response: httpx.Response | None = None
    for i in range(len(backoffs) + 1):
        if i > 0:
            await asyncio.sleep(backoffs[i - 1])
        try:
            response = await do_send()
        except TRANSIENT_TRANSPORT_EXCEPTIONS:
            if i == len(backoffs):  # retries exhausted (or non-retryable)
                raise
            continue
        if i < len(backoffs) and _is_transient_5xx(response.status_code):
            continue
        return response
    return response  # type: ignore[return-value]


def _send_sync_with_retry(
    method: str,
    do_send: Callable[[], httpx.Response],
    *,
    retry_transient: bool = False,
) -> httpx.Response:
    """Sync counterpart of :func:`_send_with_retry`."""
    retryable = _is_idempotent(method) or retry_transient
    backoffs = SDK_RETRY_BACKOFF_SECONDS if retryable else ()
    response: httpx.Response | None = None
    for i in range(len(backoffs) + 1):
        if i > 0:
            time.sleep(backoffs[i - 1])
        try:
            response = do_send()
        except TRANSIENT_TRANSPORT_EXCEPTIONS:
            if i == len(backoffs):
                raise
            continue
        if i < len(backoffs) and _is_transient_5xx(response.status_code):
            continue
        return response
    return response  # type: ignore[return-value]


def raise_for_status_with_detail(response: httpx.Response) -> None:
    """Raise httpx.HTTPStatusError with API error detail included in the message.

    Tries to parse the response JSON for ``message`` or ``error`` fields and
    appends them to the standard HTTP status text so that 4xx/5xx errors are
    immediately understandable without inspecting the body separately.

    Falls back to the standard ``response.raise_for_status()`` when the body
    cannot be parsed or contains no useful detail.
    """
    if response.is_success:
        return

    detail = ""
    try:
        body = response.json()
        detail = body.get("detail") or body.get("message") or body.get("error") or ""
    except (ValueError, AttributeError) as e:
        # Response wasn't JSON or didn't have dict-like .get — fall back to raise_for_status
        logger.debug(f"could not extract error detail from response body: {e}")

    error_type: type[BifrostAPIError]
    if response.status_code == 401:
        error_type = BifrostAuthenticationError
    elif response.status_code == 403:
        error_type = BifrostAuthorizationError
    elif response.status_code == 424:
        error_type = BifrostDependencyError
    else:
        error_type = BifrostAPIError
    message = f"{response.status_code} {response.reason_phrase}"
    if detail:
        message += f": {detail}"
    raise error_type(message=message, request=response.request, response=response)

# Thread-local storage for per-thread singleton instances
# This is needed because thread workers create new event loops via asyncio.run(),
# and httpx.AsyncClient is bound to the event loop that created it.
_thread_local = threading.local()


class _ConnectionRefreshCoordinator:
    """Loop-agnostic single-flight state for one normalized API URL."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.latest_access_token: str | None = None
        self.latest_refresh_token: str | None = None


_refresh_coordinators: dict[str, _ConnectionRefreshCoordinator] = {}
_refresh_coordinators_lock = threading.Lock()


def _refresh_coordinator(api_url: str) -> _ConnectionRefreshCoordinator:
    normalized = api_url.rstrip("/")
    with _refresh_coordinators_lock:
        coordinator = _refresh_coordinators.get(normalized)
        if coordinator is None:
            coordinator = _ConnectionRefreshCoordinator()
            _refresh_coordinators[normalized] = coordinator
        return coordinator


def _reset_refresh_coordinators_for_tests() -> None:
    """Clear process refresh state so credential tests remain isolated."""
    with _refresh_coordinators_lock:
        _refresh_coordinators.clear()


async def _acquire_refresh_lock(lock: threading.Lock) -> None:
    """Acquire a thread lock without leaking it when the waiter is cancelled."""
    acquire_task = asyncio.create_task(asyncio.to_thread(lock.acquire))
    try:
        await asyncio.shield(acquire_task)
    except asyncio.CancelledError:
        # to_thread keeps running after cancellation. Wait until it owns the lock,
        # release it, then preserve cancellation for the request task.
        if await acquire_task:
            lock.release()
        raise

# Preserve non-auth project context without merging auth fields from different
# sources into one synthetic os.environ tuple.
load_dotenv_context()


async def refresh_connection_access_token(
    api_url: str,
    observed_access_token: str | None,
) -> str | None:
    """Return the authoritative access token for one stale credential generation.

    Concurrent callers for the same connection are serialized with a threading
    lock so the coordinator works across threads and event loops. After waiting,
    a caller re-reads stored credentials and reuses a token already installed by
    another caller instead of rotating the same refresh token twice.
    """
    normalized_url = api_url.rstrip("/")
    coordinator = _refresh_coordinator(normalized_url)
    credential_source = "unknown"
    await _acquire_refresh_lock(coordinator.lock)
    try:
        resolved = resolve_credentials(normalized_url)
        if resolved is None:
            logger.warning(
                "Bifrost token refresh failed for %s: no credentials resolved",
                normalized_url,
            )
            return None
        credential_source = resolved.source
        creds = resolved.credentials

        stored_access_token = creds.access_token
        stored_refresh_token = creds.refresh_token
        process_backed = resolved.source == "process"

        if coordinator.latest_access_token is not None:
            if observed_access_token != coordinator.latest_access_token:
                return coordinator.latest_access_token
            if (
                not process_backed
                and stored_access_token != coordinator.latest_access_token
            ):
                coordinator.latest_access_token = stored_access_token
                coordinator.latest_refresh_token = stored_refresh_token
                return stored_access_token
        elif observed_access_token is not None and stored_access_token != observed_access_token:
            coordinator.latest_access_token = stored_access_token
            coordinator.latest_refresh_token = stored_refresh_token
            return stored_access_token

        refresh_token = coordinator.latest_refresh_token or stored_refresh_token

        async with httpx.AsyncClient(base_url=normalized_url, timeout=30.0) as client:
            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": refresh_token},
            )
        if response.status_code != 200:
            logger.warning(
                "Bifrost token refresh failed for %s using %s credentials: HTTP %s",
                normalized_url,
                credential_source,
                response.status_code,
            )
            return None

        data = response.json()
        access_token = str(data["access_token"])
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=data.get("expires_in", 1800)
        )
        new_refresh_token = str(data["refresh_token"])
        saved = save_refreshed_credentials(
            resolved,
            stored_access_token,
            refresh_token,
            access_token,
            new_refresh_token,
            expires_at.isoformat(),
        )
        if not saved:
            logger.warning(
                "Bifrost token refresh succeeded for %s, but the %s credential "
                "source changed before rotated tokens could be written back",
                normalized_url,
                credential_source,
            )
        coordinator.latest_access_token = access_token
        coordinator.latest_refresh_token = new_refresh_token
        return access_token
    except Exception as exc:
        logger.warning(
            "Bifrost token refresh failed for %s using %s credentials: %s",
            normalized_url,
            credential_source,
            type(exc).__name__,
        )
        return None
    finally:
        coordinator.lock.release()


async def refresh_tokens(
    api_url: str | None = None,
    observed_access_token: str | None = None,
) -> bool:
    """
    Refresh access token using refresh token.

    Uses credentials from credentials file.
    Updates credentials file with new tokens.

    Returns:
        True if refresh successful, False otherwise
    """
    creds = get_credentials(api_url)
    if not creds:
        return False
    observed = observed_access_token or str(creds["access_token"])
    token = await refresh_connection_access_token(str(creds["api_url"]), observed)
    return token is not None


def _refresh_tokens_sync(
    api_url: str | None = None,
    observed_access_token: str | None = None,
) -> bool:
    creds = get_credentials(api_url)
    if not creds:
        return False
    token = _refresh_connection_access_token_sync(
        str(creds["api_url"]),
        observed_access_token or str(creds["access_token"]),
    )
    return token is not None


def _refresh_connection_access_token_sync(
    api_url: str,
    observed_access_token: str,
) -> str | None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            refresh_connection_access_token(api_url, observed_access_token)
        )

    result: str | None = None
    error: Exception | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(
                refresh_connection_access_token(api_url, observed_access_token)
            )
        except Exception as exc:
            error = exc

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()

    if error is not None:
        raise error
    return result


async def login_flow(api_url: str | None = None, auto_open: bool = True) -> bool:
    """
    Interactive device authorization flow.

    1. Request device code from API
    2. Display user code and verification URL
    3. Open browser automatically (if auto_open=True)
    4. Poll for authorization
    5. Save credentials when authorized

    Args:
        api_url: Bifrost API URL (uses BIFROST_API_URL env var if not provided)
        auto_open: Whether to automatically open browser (default: True)

    Returns:
        True if login successful, False otherwise
    """
    # Get API URL
    if not api_url:
        api_url = resolve_environment_url() or "http://localhost:8000"

    api_url = api_url.rstrip("/")

    # Surface keyring fallback here — login is the user's chance to fix it.
    from bifrost.credentials import warn_if_keyring_fallback
    warn_if_keyring_fallback()

    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as client:
            # Step 1: Request device code
            response = await client.post("/auth/device/code")
            if response.status_code != 200:
                print(f"Error requesting device code: {response.status_code}", file=sys.stderr)
                return False

            data = response.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_url = data["verification_url"]
            interval = data.get("interval", 5)

            # Step 2: Display instructions
            full_url = f"{api_url}{verification_url}"
            print(f"\nOpening browser to {full_url}")
            print(f"Enter this code: {user_code}\n")

            # Step 3: Open browser
            if auto_open:
                try:
                    webbrowser.open(full_url)
                except Exception:
                    pass  # Ignore browser open failures

            # Step 4: Poll for authorization
            print("Waiting for authorization", end="", flush=True)
            max_attempts = 60  # 5 minutes max (60 * 5 seconds)
            attempts = 0

            while attempts < max_attempts:
                await asyncio.sleep(interval)
                print(".", end="", flush=True)
                attempts += 1

                poll_response = await client.post(
                    "/auth/device/token",
                    json={"device_code": device_code}
                )

                if poll_response.status_code != 200:
                    print(f"\nError polling for token: {poll_response.status_code}", file=sys.stderr)
                    return False

                poll_data = poll_response.json()

                # Check for error
                if "error" in poll_data:
                    error = poll_data["error"]
                    if error == "authorization_pending":
                        continue  # Keep polling
                    elif error == "expired_token":
                        print("\nDevice code expired. Please try again.", file=sys.stderr)
                        return False
                    elif error == "access_denied":
                        print("\nAuthorization denied.", file=sys.stderr)
                        return False
                    else:
                        print(f"\nUnknown error: {error}", file=sys.stderr)
                        return False

                # Success - we have tokens
                if "access_token" in poll_data:
                    print(" OK")

                    # Calculate expiry time
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=poll_data.get("expires_in", 1800))

                    # Step 5: Save credentials
                    save_credentials(
                        api_url=api_url,
                        access_token=poll_data["access_token"],
                        refresh_token=poll_data["refresh_token"],
                        expires_at=expires_at.isoformat(),
                    )

                    # Get user info
                    try:
                        user_response = await client.get(
                            "/auth/me",
                            headers={"Authorization": f"Bearer {poll_data['access_token']}"}
                        )
                        if user_response.status_code == 200:
                            user_data = user_response.json()
                            print(f"Logged in as {user_data.get('email', 'unknown')}\n")
                    except Exception:
                        print("Logged in successfully\n")

                    return True

            print("\nTimeout waiting for authorization.", file=sys.stderr)
            return False

    except Exception as e:
        print(f"\nLogin failed: {e}", file=sys.stderr)
        return False


def logout() -> bool:
    """
    Logout by clearing stored credentials.

    Returns:
        True if credentials were cleared, False if no credentials existed
    """
    creds = get_credentials()
    if creds:
        clear_credentials()
        print("Logged out successfully.")
        return True
    else:
        print("No active session found.")
        return False


class BifrostClient:
    """
    HTTP client for Bifrost API.

    Used in two modes:
    - Platform mode: Injected by workflow engine via _set_client()
    - CLI mode: Singleton pattern via get_instance()
    """

    def __init__(self, api_url: str, access_token: str):
        """
        Initialize client.

        Args:
            api_url: Bifrost API URL
            access_token: JWT access token (device auth for CLI, execution token for platform)
        """
        self.api_url = api_url.rstrip("/")
        self._access_token = access_token
        # Async client is created lazily per event loop to handle thread workers
        # that create new event loops via asyncio.run() on each execution
        self._http: httpx.AsyncClient | None = None
        self._http_loop: asyncio.AbstractEventLoop | None = None
        self._sync_http = httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        # Engine-local Unix-socket transport, built lazily from the trusted
        # worker injection and kept separate from the network clients above.
        # Only :meth:`engine_request` / :meth:`engine_request_sync` use these;
        # the recorded socket path lets a client rebuild if the injection
        # changes.
        self._engine_http: httpx.AsyncClient | None = None
        self._engine_http_loop: asyncio.AbstractEventLoop | None = None
        self._engine_http_path: str | None = None
        self._engine_sync_http: httpx.Client | None = None
        self._engine_sync_http_path: str | None = None
        self._context: dict[str, Any] | None = None

    def _get_async_client(self) -> httpx.AsyncClient:
        """
        Get httpx.AsyncClient, creating fresh one if needed for current event loop.

        Thread workers use asyncio.run() which creates/destroys event loops per execution.
        httpx.AsyncClient is bound to the event loop that created it, so we need to
        create a new client when the event loop changes.
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - create client anyway, it will bind when first used
            current_loop = None

        # Check if we need a new client (no client, or different event loop)
        if self._http is None or (current_loop is not None and self._http_loop != current_loop):
            # Old client (if any) will be garbage-collected; httpx handles
            # transport cleanup at GC time. Can't await aclose() from a sync
            # method, so this is the best we can do.

            # Create new client bound to current event loop
            self._http = httpx.AsyncClient(
                base_url=self.api_url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30.0,
            )
            self._http_loop = current_loop

        return self._http

    def _get_engine_async_client(self) -> httpx.AsyncClient:
        """Async HTTPX client bound to the trusted worker Unix socket.

        Built lazily and cached per event loop (and per injected socket path),
        exactly like :meth:`_get_async_client`, so concurrent async SDK calls
        and streaming share one connection pool instead of constructing a new
        client per request. Raises when no socket is injected: an opted-in
        local request never silently falls back to the network API.
        """
        socket_path = _engine_socket_path
        if socket_path is None:
            raise RuntimeError(
                "engine socket transport is not installed; refusing to send "
                "an engine-local request without the trusted worker injection"
            )
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - create client anyway, it will bind when first used
            current_loop = None
        if (
            self._engine_http is None
            or self._engine_http_path != socket_path
            or (current_loop is not None and self._engine_http_loop != current_loop)
        ):
            self._engine_http = httpx.AsyncClient(
                base_url=_ENGINE_SOCKET_BASE_URL,
                transport=httpx.AsyncHTTPTransport(uds=socket_path),
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30.0,
            )
            self._engine_http_loop = current_loop
            self._engine_http_path = socket_path
        return self._engine_http

    def _get_engine_sync_client(self) -> httpx.Client:
        """Synchronous HTTPX client bound to the trusted worker Unix socket.

        Mirrors :meth:`_get_engine_async_client` for the sync SDK surface (cold
        module import and client context reads). Raises when no socket is
        injected so an opted-in local request never falls back to the network.
        """
        socket_path = _engine_socket_path
        if socket_path is None:
            raise RuntimeError(
                "engine socket transport is not installed; refusing to send "
                "an engine-local request without the trusted worker injection"
            )
        if self._engine_sync_http is None or self._engine_sync_http_path != socket_path:
            if self._engine_sync_http is not None:
                self._engine_sync_http.close()
            self._engine_sync_http = httpx.Client(
                base_url=_ENGINE_SOCKET_BASE_URL,
                transport=httpx.HTTPTransport(uds=socket_path),
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30.0,
            )
            self._engine_sync_http_path = socket_path
        return self._engine_sync_http

    def _async_http_for(self, *, engine_local: bool) -> httpx.AsyncClient:
        """Pick the network or engine-local async client for one request.

        ``engine_local`` selects the trusted worker socket when one is
        injected. With no injection this is not an engine child, so the normal
        network client is correct; once injected, transport failures raise
        instead of being replayed over the network.
        """
        if engine_local and _engine_socket_path is not None:
            return self._get_engine_async_client()
        return self._get_async_client()

    def _sync_http_for(self, *, engine_local: bool) -> httpx.Client:
        """Synchronous counterpart to :meth:`_async_http_for`."""
        if engine_local and _engine_socket_path is not None:
            return self._get_engine_sync_client()
        return self._sync_http

    @classmethod
    def get_instance(
        cls,
        require_auth: bool = False,
        *,
        api_url: str | None = None,
    ) -> "BifrostClient":
        """
        Get thread-local singleton client instance.

        Uses thread-local storage so each thread gets its own instance with
        its own httpx.AsyncClient bound to the thread's event loop. This is
        necessary because thread workers create new event loops via asyncio.run().

        Auto-initializes from credentials file (~/.bifrost/credentials.json)
        stored by 'bifrost login'.

        Args:
            require_auth: If True, trigger interactive login when no credentials found
                         (default: False for backward compatibility)
            api_url: Explicit instance whose URL-keyed credentials must be used.
                     This bypasses ambient directory/default-profile selection.

        Returns:
            BifrostClient instance

        Raises:
            RuntimeError: If no credentials file exists
        """
        # Use thread-local storage instead of class-level singleton
        # This ensures each thread gets its own client with httpx bound to its event loop
        selected_api_url = api_url.rstrip("/") if api_url else None
        instance = getattr(_thread_local, 'bifrost_client', None)
        if instance is not None and (
            selected_api_url is None or instance.api_url == selected_api_url
        ):
            return instance

        if instance is None or selected_api_url is not None:
            # Try credentials file from CLI login
            creds = get_credentials(
                selected_api_url,
                prompt_for_default=require_auth and selected_api_url is None,
            )

            # Check if token needs refresh
            if creds and is_token_expired(api_url=creds["api_url"]):
                # Try to refresh
                access_token = _refresh_connection_access_token_sync(
                    creds["api_url"], creds["access_token"]
                )
                if access_token is not None:
                    # Use the coordinator result directly so startup and
                    # request-time refresh share the same token generation.
                    creds = {**creds, "access_token": access_token}
                else:
                    creds = None  # Refresh failed, need to re-login

            if creds:
                # Use credentials from file
                instance = cls(creds["api_url"], creds["access_token"])
                _thread_local.bifrost_client = instance
                return instance

            # No credentials - trigger login flow if required
            if require_auth:
                selected_url, _selected_source = resolve_current_connection(
                    selected_api_url,
                    prompt_for_default=selected_api_url is None,
                )
                if selected_url is None:
                    stored_urls = []
                    try:
                        from bifrost.credentials import list_credentials
                        stored_urls = list_credentials()
                    except Exception:
                        stored_urls = []
                    if stored_urls:
                        raise RuntimeError(
                            "Multiple Bifrost connections are stored, but no default "
                            "connection is selected. Run 'bifrost auth use <url>' "
                            "or rerun in an interactive terminal to choose one."
                        )
                try:
                    # If a loop is already running we're in an async context
                    # (e.g. tests). Don't trigger interactive login.
                    asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop, safe to use asyncio.run()
                    if asyncio.run(login_flow(selected_url)):
                        # Login successful, load credentials
                        creds = get_credentials(selected_url)
                        if creds:
                            instance = cls(creds["api_url"], creds["access_token"])
                            _thread_local.bifrost_client = instance
                            return instance
                else:
                    raise RuntimeError(
                        "Not logged in. Run 'bifrost login' to authenticate."
                    )

            # No auth available
            raise RuntimeError(
                "Not logged in. Run 'bifrost login' to authenticate."
            )

        return instance

    def _fetch_context_sync(self) -> dict[str, Any]:
        """Fetch development context synchronously.

        Reads ``GET /api/sdk/context`` through the single engine-local entry
        point: the trusted worker socket when the engine injected one, the
        ordinary network client otherwise. The synchronous HTTPX request runs
        on the calling thread over its own connection, so a property read
        cannot deadlock a running child event loop even while an async SDK
        call is in flight. Once the socket is injected a local attempt never
        falls back to the network; status mapping is the shared HTTP mapping.
        """
        if self._context is None:
            response = self.engine_request_sync("GET", "/api/sdk/context")
            raise_for_status_with_detail(response)
            self._context = response.json()
        return self._context or {}

    async def _fetch_context(self) -> dict[str, Any]:
        """Fetch development context.

        Reads the same ``GET /api/sdk/context`` route through
        :meth:`engine_request`, which resolves to the worker socket when the
        engine injected one and the ordinary network client otherwise. Cached
        after the first fetch; statuses map through the shared HTTP mapping.
        """
        if self._context is None:
            response = await self.engine_request("GET", "/api/sdk/context")
            raise_for_status_with_detail(response)
            self._context = response.json()
        return self._context or {}

    @property
    def context(self) -> dict[str, Any]:
        """Get cached development context (fetches synchronously if needed)."""
        return self._fetch_context_sync()

    @property
    def user(self) -> dict[str, Any]:
        """Get current user info."""
        return self.context.get("user", {})

    @property
    def organization(self) -> dict[str, Any] | None:
        """Get default organization."""
        return self.context.get("organization")

    @property
    def default_parameters(self) -> dict[str, Any]:
        """Get default workflow parameters."""
        return self.context.get("default_parameters", {})

    def install_access_token(self, access_token: str) -> None:
        """Install one access token across every transport owned by this client."""
        if access_token == self._access_token:
            return
        self._access_token = access_token
        # Force a new async client on the next request. An in-flight request may
        # still finish on the old client, but its retry resolves a fresh one.
        self._http = None
        self._http_loop = None
        self._sync_http.headers["Authorization"] = f"Bearer {access_token}"
        # Engine-local clients carry the same bearer token; drop them rather
        # than mutate so an in-flight local request cannot observe a
        # half-updated header. They rebuild lazily from the new token.
        self._engine_http = None
        self._engine_http_loop = None
        self._engine_http_path = None
        if self._engine_sync_http is not None:
            self._engine_sync_http.close()
            self._engine_sync_http = None
            self._engine_sync_http_path = None

    async def refresh_access_token(
        self, observed_access_token: str | None = None
    ) -> str | None:
        """Refresh or adopt the token replacing the one used by a failed request."""
        observed = observed_access_token or self._access_token
        token = await refresh_connection_access_token(self.api_url, observed)
        if token is not None:
            self.install_access_token(token)
        return token

    async def _refresh_and_update(
        self, observed_access_token: str | None = None
    ) -> bool:
        """Backward-compatible boolean wrapper used by CLI request loops."""
        return await self.refresh_access_token(observed_access_token) is not None

    def _refresh_and_update_sync(
        self, observed_access_token: str | None = None
    ) -> bool:
        """Synchronous counterpart used by context and legacy SDK requests."""
        observed = observed_access_token or self._access_token
        token = _refresh_connection_access_token_sync(self.api_url, observed)
        if token is None:
            return False
        self.install_access_token(token)
        return True

    def _request_with_refresh_sync(
        self, method: str, path: str, *, engine_local: bool = False, **kwargs
    ) -> httpx.Response:
        """Make a synchronous request, refreshing on 401 and retrying once.

        ``engine_local=True`` sends the request over the trusted worker Unix
        socket when one is injected. A local attempt never falls back to the
        network API, and no network token refresh is attempted for it: the
        child's engine token is handed to the process and is not refreshable
        from the child.
        """
        retry_transient = kwargs.pop("retry_transient", False)
        use_engine = engine_local and _engine_socket_path is not None

        def _send() -> httpx.Response:
            http = self._sync_http_for(engine_local=engine_local)
            observed_access_token = self._access_token
            response = http.request(method.upper(), path, **kwargs)
            if (
                not use_engine
                and response.status_code == 401
                and self._refresh_and_update_sync(observed_access_token)
            ):
                response = self._sync_http.request(method.upper(), path, **kwargs)
            return response

        return _send_sync_with_retry(method, _send, retry_transient=retry_transient)

    async def _request_with_refresh(
        self, method: str, path: str, *, engine_local: bool = False, **kwargs
    ) -> httpx.Response:
        """Make an HTTP request, refreshing token on 401 and retrying once.

        ``engine_local=True`` sends the request over the trusted worker Unix
        socket when one is injected (see
        :meth:`_request_with_refresh_sync` for the no-fallback contract).

        Wrapped with :func:`_send_with_retry` so idempotent methods (or callers passing
        ``retry_transient=True``) retry transient 502/503/504 and transport errors during rolling API deploys. The 401-refresh-retry
        fires inside each attempt, so a refresh-then-5xx still benefits from
        the outer retry.
        """
        retry_transient = kwargs.pop("retry_transient", False)
        use_engine = engine_local and _engine_socket_path is not None

        async def _send() -> httpx.Response:
            http = self._async_http_for(engine_local=engine_local)
            observed_access_token = self._access_token
            response = await getattr(http, method)(path, **kwargs)
            if (
                not use_engine
                and response.status_code == 401
                and await self._refresh_and_update(observed_access_token)
            ):
                http = self._get_async_client()
                response = await getattr(http, method)(path, **kwargs)
            return response

        return await _send_with_retry(method, _send, retry_transient=retry_transient)

    async def engine_request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        """Send one request over the injected engine-local transport.

        Single engine-local entry point for SDK facades: it resolves the
        transport once (the worker Unix socket when the engine injected one,
        the ordinary network client otherwise) and is otherwise identical to
        :meth:`request` in retry, error, and public-exception behavior. A local
        attempt never falls back to the network API and does not attempt a
        network token refresh.
        """
        return await self._request_with_refresh(
            method.lower(), path, engine_local=True, **kwargs
        )

    def engine_request_sync(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Synchronous counterpart to :meth:`engine_request`."""
        return self._request_with_refresh_sync(
            method, path, engine_local=True, **kwargs
        )

    async def get(self, path: str, **kwargs) -> httpx.Response:
        """Make GET request."""
        return await self._request_with_refresh("get", path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        """Make POST request."""
        return await self._request_with_refresh("post", path, **kwargs)

    async def put(self, path: str, **kwargs) -> httpx.Response:
        """Make PUT request."""
        return await self._request_with_refresh("put", path, **kwargs)

    async def patch(self, path: str, **kwargs) -> httpx.Response:
        """Make PATCH request."""
        return await self._request_with_refresh("patch", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        """Make DELETE request.

        httpx's ``AsyncClient.delete()`` does not accept a ``json=`` body;
        if you need to send a body with DELETE, use :meth:`request` instead.
        """
        return await self._request_with_refresh("delete", path, **kwargs)

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an arbitrary-method HTTP request with token refresh.

        Needed for verbs whose shortcut method on ``httpx.AsyncClient`` does
        not accept a body (DELETE) but whose REST endpoint expects one.

        Wrapped with :func:`_send_with_retry` so idempotent methods (or callers passing
        ``retry_transient=True``) retry transient 502/503/504 and transport errors during rolling API deploys.
        """
        retry_transient = kwargs.pop("retry_transient", False)

        async def _send() -> httpx.Response:
            http = self._get_async_client()
            observed_access_token = self._access_token
            response = await http.request(method.upper(), path, **kwargs)
            if response.status_code == 401:
                if await self._refresh_and_update(observed_access_token):
                    http = self._get_async_client()
                    response = await http.request(method.upper(), path, **kwargs)
            return response

        return await _send_with_retry(method, _send, retry_transient=retry_transient)

    def stream(self, method: str, path: str, **kwargs):
        """
        Create an async streaming request context manager.

        Usage:
            async with client.stream("POST", "/path", json={...}) as response:
                async for line in response.aiter_lines():
                    process(line)
        """
        return self._get_async_client().stream(method, path, **kwargs)

    def engine_stream(self, method: str, path: str, **kwargs):
        """Open a streaming request over the engine-local transport.

        Shares :meth:`engine_request`'s single transport choice: the trusted
        worker Unix socket when the engine injected one, the ordinary network
        client otherwise. The transport is selected once, before the request
        is sent, so a local attempt never falls back to the network API after
        a socket failure.

        Timeout behavior is the external HTTP path's exactly: both cached
        clients are built with HTTPX ``timeout=30.0``, so the per-read gap
        bound is 30 seconds on either transport. No SDK-level stream or
        channel deadline is added here; a slow provider that keeps sending
        within that gap runs to completion.
        """
        http = self._async_http_for(engine_local=True)
        return http.stream(method, path, **kwargs)

    def get_sync(self, path: str, **kwargs) -> httpx.Response:
        """Make synchronous GET request.

        Wrapped with :func:`_send_sync_with_retry` so transient 502/503/504
        from rolling API deploys are retried.
        """
        return self._request_with_refresh_sync("GET", path, **kwargs)

    def post_sync(self, path: str, **kwargs) -> httpx.Response:
        """Make synchronous POST request."""
        return self._request_with_refresh_sync("POST", path, **kwargs)

    async def close(self):
        """Close HTTP clients."""
        if self._http is not None:
            await self._http.aclose()
        if self._engine_http is not None:
            await self._engine_http.aclose()
        self._sync_http.close()
        if self._engine_sync_http is not None:
            self._engine_sync_http.close()


def _set_client(client: BifrostClient) -> None:
    """
    Inject client for platform mode.

    Called by workflow engine before executing workflow code.
    This allows SDK calls to use an authenticated client without
    needing credentials file.

    Args:
        client: BifrostClient instance with execution token
    """
    global _injected_client
    _injected_client = client


def _clear_client() -> None:
    """
    Clear injected client after workflow execution.

    Called by workflow engine in finally block to clean up
    after workflow execution completes.
    """
    global _injected_client
    _injected_client = None


def get_client() -> BifrostClient:
    """
    Get the active Bifrost client.

    Returns injected client if available (platform mode),
    otherwise falls back to singleton from credentials file (CLI mode).

    Returns:
        BifrostClient instance

    Raises:
        RuntimeError: If no injected client and no credentials file
    """
    global _injected_client

    # Platform mode: use injected client
    if _injected_client is not None:
        return _injected_client

    # CLI mode: use singleton from credentials
    return BifrostClient.get_instance()


def has_credentials() -> bool:
    """Check if API credentials are available (without triggering login flow).

    Returns True if a valid credentials file exists from previous 'bifrost login'.
    """
    creds = get_credentials()
    return creds is not None


def _install_engine_socket(path: str) -> None:
    """Install the trusted worker socket path for this child (engine start).

    Called by the execution engine in the forked child before user code
    runs. There is no user-facing flag: outside this injection the socket
    transport is absent and the SDK uses its normal transport.
    """
    global _engine_socket_path
    _engine_socket_path = path or None


def _clear_engine_socket() -> None:
    """Drop the injected worker socket path at engine teardown."""
    global _engine_socket_path
    _engine_socket_path = None


def get_engine_socket_path() -> str | None:
    """Return the injected worker socket path, or None when not injected."""
    return _engine_socket_path
