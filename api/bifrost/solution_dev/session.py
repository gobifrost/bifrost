"""Renewable, user-bound provenance for a local Solution development session."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from bifrost.client import raise_for_status_with_detail

if TYPE_CHECKING:
    from bifrost.client import BifrostClient


DEV_CONTEXT_HEADER = "X-Bifrost-Dev-Context"


class SolutionDevSession:
    """Keep the signed install context out of the browser and credential store.

    The ordinary CLI bearer token authenticates every request, including renewal.
    This context only proves which install the local source belongs to.
    """

    def __init__(self, client: BifrostClient, solution_id: str, app_id: str) -> None:
        self.client = client
        self.solution_id = solution_id
        self.manifest_app_id = app_id
        self.app_id = app_id
        self.organization_id: str | None = None
        self.endpoint = f"/api/solutions/{solution_id}/dev-context"
        self._token = ""
        self._expires_at = 0.0

    def _install(self, body: dict[str, Any]) -> None:
        if str(body["solution_id"]) != self.solution_id:
            raise ValueError("The development context belongs to another Solution install")
        self.app_id = str(body["app_id"])
        self.organization_id = body["organization_id"]
        self._token = body["token"]
        self._expires_at = float(body["expires_at"])

    async def headers(self) -> dict[str, str]:
        if time.time() >= self._expires_at - 30:
            response = await self.client.post(
                self.endpoint, json={"app_id": self.manifest_app_id}
            )
            raise_for_status_with_detail(response)
            self._install(response.json())
        return {DEV_CONTEXT_HEADER: self._token}

    def headers_sync(self) -> dict[str, str]:
        if time.time() >= self._expires_at - 30:
            response = self.client.post_sync(
                self.endpoint, json={"app_id": self.manifest_app_id}
            )
            raise_for_status_with_detail(response)
            self._install(response.json())
        return {DEV_CONTEXT_HEADER: self._token}
