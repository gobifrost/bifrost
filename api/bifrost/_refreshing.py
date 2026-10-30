"""Challenge-driven token refresh for every HTTPX client the SDK creates.

The API refuses an access token that a refresh would fix (an expired token,
or a runtime token minted under an older authority epoch) with::

    401  WWW-Authenticate: Bearer error="invalid_token", error_description="refresh_required"

Its authentication dependency raises that challenge before any route handler
or later dependency runs, so a challenged request has had no effect and is
safe to replay. No other 401 is ever replayed: an authentication error
mapped to 401 after a handler ran (for example an AI provider's) surfaces as
it is.

Each public SDK call carries one :class:`RefreshBudget` of
``REFRESH_BUDGET_PER_CALL`` refreshes in its request extensions. Every
attempt of the call, including the SDK's transient-retry attempts, shares it,
so a call refreshes at most three times however often it is retried. A
request sent without a budget is never replayed.

The refresh-and-replay happens in :meth:`httpx.Client.send`, where both
unary and streaming requests pass, and where a streamed response has been
opened but none of its body exposed to the caller: once a response is
returned it is never replayed. Overriding ``send`` rather than wrapping the
client's transport keeps HTTPX's environment proxy support, which HTTPX
turns off whenever a transport is passed in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

REFRESH_BUDGET_EXTENSION = "bifrost_refresh_budget"
REFRESH_BUDGET_PER_CALL = 3


class RefreshBudget:
    """Challenge-driven refreshes left for one public SDK call."""

    __slots__ = ("remaining",)

    def __init__(self, remaining: int = REFRESH_BUDGET_PER_CALL) -> None:
        self.remaining = remaining

    def take(self) -> bool:
        """Spend one refresh; False once the call has none left."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def refresh_extensions(extensions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Request extensions for one public SDK call, with a fresh budget.

    Pass the same dictionary to every attempt of the call so the attempts
    share the budget.
    """
    return {**(extensions or {}), REFRESH_BUDGET_EXTENSION: RefreshBudget()}


def is_refresh_challenge(response: httpx.Response) -> bool:
    """Whether a response is the API's ``refresh_required`` challenge."""
    if response.status_code != 401:
        return False
    challenge = response.headers.get("www-authenticate", "")
    return 'error="invalid_token"' in challenge and 'error_description="refresh_required"' in challenge


def _budget(request: httpx.Request) -> RefreshBudget | None:
    budget = request.extensions.get(REFRESH_BUDGET_EXTENSION)
    return budget if isinstance(budget, RefreshBudget) else None


def _bearer(request: httpx.Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    return authorization[7:] if authorization.lower().startswith("bearer ") else None


AsyncRefresh = Callable[[str | None], Awaitable[str | None]]
SyncRefresh = Callable[[str | None], str | None]


class RefreshingAsyncClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that refreshes and replays on the challenge.

    ``refresh`` receives the access token the challenged request carried and
    returns the token to replay with, or None when no refresh is possible
    (the challenge is then returned to the caller unchanged).
    """

    def __init__(self, *args: Any, refresh: AsyncRefresh, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._refresh = refresh

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await super().send(request, **kwargs)
        budget = _budget(request)
        while budget is not None and is_refresh_challenge(response) and budget.take():
            token = await self._refresh(_bearer(request))
            if token is None:
                return response
            await response.aclose()
            request.headers["Authorization"] = f"Bearer {token}"
            response = await super().send(request, **kwargs)
        return response


class RefreshingClient(httpx.Client):
    """Synchronous counterpart of :class:`RefreshingAsyncClient`."""

    def __init__(self, *args: Any, refresh: SyncRefresh, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._refresh = refresh

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = super().send(request, **kwargs)
        budget = _budget(request)
        while budget is not None and is_refresh_challenge(response) and budget.take():
            token = self._refresh(_bearer(request))
            if token is None:
                return response
            response.close()
            request.headers["Authorization"] = f"Bearer {token}"
            response = super().send(request, **kwargs)
        return response
