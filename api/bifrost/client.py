"""
Bifrost SDK Client

HTTP client for Bifrost API communication.
Supports two modes:
1. Platform mode: Client is injected by workflow engine
2. CLI mode: Auto-initializes from credentials file stored by 'bifrost login'
"""

import asyncio
import logging
import os
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
    save_credentials,
)

logger = logging.getLogger(__name__)

# Global client injection for platform mode
_injected_client: Optional["BifrostClient"] = None

# Retry config for transient 5xx — see workstream E of issue #171.
# SDK is machine-to-machine, so the retry budget is more generous than
# the user-facing client.
TRANSIENT_5XX_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})
IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "PUT", "DELETE", "HEAD", "OPTIONS"})
SDK_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.5, 4.0, 10.0, 20.0)


def _is_idempotent(method: str) -> bool:
    return method.upper() in IDEMPOTENT_METHODS


def _is_transient_5xx(status_code: int) -> bool:
    return status_code in TRANSIENT_5XX_STATUS_CODES


async def _send_with_5xx_retry(
    method: str,
    do_send: Callable[[], Awaitable[httpx.Response]],
) -> httpx.Response:
    """Retry transient 5xx on idempotent methods.

    The do_send callable must be safe to invoke multiple times; it should
    re-issue the request fresh each call (httpx Request objects with bodies
    are single-use, so callers either pass simple methods or rebuild the
    request inside the closure).
    """
    response = await do_send()
    if not _is_idempotent(method):
        return response

    for delay in SDK_RETRY_BACKOFF_SECONDS:
        if not _is_transient_5xx(response.status_code):
            return response
        await asyncio.sleep(delay)
        response = await do_send()
    return response


def _send_sync_with_5xx_retry(
    method: str,
    do_send: Callable[[], httpx.Response],
) -> httpx.Response:
    """Sync counterpart of _send_with_5xx_retry."""
    response = do_send()
    if not _is_idempotent(method):
        return response

    for delay in SDK_RETRY_BACKOFF_SECONDS:
        if not _is_transient_5xx(response.status_code):
            return response
        time.sleep(delay)
        response = do_send()
    return response


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

    if detail:
        raise httpx.HTTPStatusError(
            message=f"{response.status_code} {response.reason_phrase}: {detail}",
            request=response.request,
            response=response,
        )

    response.raise_for_status()

# Thread-local storage for per-thread singleton instances
# This is needed because thread workers create new event loops via asyncio.run(),
# and httpx.AsyncClient is bound to the event loop that created it.
_thread_local = threading.local()

# Auto-load .env file if present (for local development).
# Walk upward from cwd, not from this file. With pipx-installed CLIs, __file__
# lives in the pipx venv and the default upward walk never reaches the user's
# workspace, so a .env in the project root is silently ignored.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass  # dotenv not installed, rely on environment variables


async def refresh_tokens() -> bool:
    """
    Refresh access token using refresh token.

    Uses credentials from credentials file.
    Updates credentials file with new tokens.

    Returns:
        True if refresh successful, False otherwise
    """
    creds = get_credentials()
    if not creds:
        return False

    api_url = creds["api_url"]
    refresh_token = creds["refresh_token"]

    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as client:
            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": refresh_token}
            )

            if response.status_code != 200:
                return False

            data = response.json()

            # Calculate expiry time (30 minutes from now)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 1800))

            # Save new credentials
            save_credentials(
                api_url=api_url,
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=expires_at.isoformat(),
            )

            return True
    except Exception:
        return False


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
        api_url = os.getenv("BIFROST_API_URL", "http://localhost:8000")

    api_url = api_url.rstrip("/")

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

    @classmethod
    def get_instance(cls, require_auth: bool = False) -> "BifrostClient":
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

        Returns:
            BifrostClient instance

        Raises:
            RuntimeError: If no credentials file exists
        """
        # Use thread-local storage instead of class-level singleton
        # This ensures each thread gets its own client with httpx bound to its event loop
        instance = getattr(_thread_local, 'bifrost_client', None)

        if instance is None:
            # Try credentials file from CLI login
            creds = get_credentials()

            # Check if token needs refresh
            if creds and is_token_expired():
                # Try to refresh
                try:
                    # Try to get existing event loop
                    asyncio.get_running_loop()
                    # We're in an async context, can't use asyncio.run()
                    # For now, just skip auto-refresh in this case
                    creds = None  # Will trigger re-login if require_auth=True
                except RuntimeError:
                    # No running loop, safe to use asyncio.run()
                    if asyncio.run(refresh_tokens()):
                        creds = get_credentials()  # Reload fresh credentials
                    else:
                        creds = None  # Refresh failed, need to re-login

            if creds:
                # Use credentials from file
                instance = cls(creds["api_url"], creds["access_token"])
                _thread_local.bifrost_client = instance
                return instance

            # No credentials - trigger login flow if required
            if require_auth:
                try:
                    # If a loop is already running we're in an async context
                    # (e.g. tests). Don't trigger interactive login.
                    asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop, safe to use asyncio.run()
                    if asyncio.run(login_flow()):
                        # Login successful, load credentials
                        creds = get_credentials()
                        if creds:
                            instance = cls(creds["api_url"], creds["access_token"])
                            _thread_local.bifrost_client = instance
                            return instance

            # No auth available
            raise RuntimeError(
                "Not logged in. Run 'bifrost login' to authenticate."
            )

        return instance

    def _fetch_context_sync(self) -> dict[str, Any]:
        """Fetch development context synchronously."""
        if self._context is None:
            response = self._sync_http.get("/api/cli/context")
            raise_for_status_with_detail(response)
            self._context = response.json()
        return self._context or {}

    async def _fetch_context(self) -> dict[str, Any]:
        """Fetch development context."""
        if self._context is None:
            http = self._get_async_client()
            response = await http.get("/api/cli/context")
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

    async def _refresh_and_update(self) -> bool:
        """Refresh tokens and update this client's auth headers."""
        if await refresh_tokens():
            creds = get_credentials()
            if creds:
                self._access_token = creds["access_token"]
                # Force new async client on next request (with new token)
                self._http = None
                # Update sync client headers too
                self._sync_http.headers["Authorization"] = f"Bearer {self._access_token}"
                return True
        return False

    async def _request_with_refresh(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an HTTP request, refreshing token on 401 and retrying once.

        Wrapped with :func:`_send_with_5xx_retry` so idempotent methods retry
        transient 502/503/504 during rolling API deploys. The 401-refresh-retry
        fires inside each attempt, so a refresh-then-5xx still benefits from
        the outer retry.
        """
        async def _send() -> httpx.Response:
            http = self._get_async_client()
            response = await getattr(http, method)(path, **kwargs)
            if response.status_code == 401:
                if await self._refresh_and_update():
                    http = self._get_async_client()
                    response = await getattr(http, method)(path, **kwargs)
            return response

        return await _send_with_5xx_retry(method, _send)

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

        Wrapped with :func:`_send_with_5xx_retry` so idempotent methods retry
        transient 502/503/504 during rolling API deploys.
        """
        async def _send() -> httpx.Response:
            http = self._get_async_client()
            response = await http.request(method.upper(), path, **kwargs)
            if response.status_code == 401:
                if await self._refresh_and_update():
                    http = self._get_async_client()
                    response = await http.request(method.upper(), path, **kwargs)
            return response

        return await _send_with_5xx_retry(method, _send)

    def stream(self, method: str, path: str, **kwargs):
        """
        Create an async streaming request context manager.

        Usage:
            async with client.stream("POST", "/path", json={...}) as response:
                async for line in response.aiter_lines():
                    process(line)
        """
        return self._get_async_client().stream(method, path, **kwargs)

    def get_sync(self, path: str, **kwargs) -> httpx.Response:
        """Make synchronous GET request.

        Wrapped with :func:`_send_sync_with_5xx_retry` so transient 502/503/504
        from rolling API deploys are retried.
        """
        return _send_sync_with_5xx_retry("GET", lambda: self._sync_http.get(path, **kwargs))

    def post_sync(self, path: str, **kwargs) -> httpx.Response:
        """Make synchronous POST request."""
        return self._sync_http.post(path, **kwargs)

    async def close(self):
        """Close HTTP clients."""
        if self._http is not None:
            await self._http.aclose()
        self._sync_http.close()


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
