"""Bounded adapter for Rewst's observed, read-only GraphQL contract.

The bundled ``graphql`` module owns all HTTP, cookie, timeout, retry and
read-only validation behaviour. This module declares only historical generic
read queries and never writes to Rewst.
"""

from __future__ import annotations

from typing import Any

from . import graphql


class RewstReadError(RuntimeError):
    """A sanitized source-read failure."""


class RewstReadClient:
    def __init__(
        self, *, endpoint: str, session: str, transport: graphql.Transport | None = None
    ) -> None:
        self._endpoint = graphql.validate_endpoint(endpoint)
        self._session = session
        self._transport = transport

    def query(
        self, query: str, operation_name: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        # ``fetch_request`` is deliberately a low-level helper. Validate here
        # because this adapter constructs its request definition in memory.
        try:
            declared = graphql.validate_read_only_query(query)
            if set(variables) - declared:
                raise graphql.ValidationError(
                    "query variables did not match the observed read query"
                )
        except graphql.ValidationError as error:
            raise RewstReadError("Rewst query compatibility check failed") from error
        request = graphql.RequestDefinition(
            name="migration_read",
            query=query,
            operation_name=operation_name,
            variables=variables,
            result_path=("data",),
            pagination=None,
            org_references=None,
        )
        try:
            data, _pages = graphql.fetch_request(
                request,
                self._endpoint,
                self._session,
                transport=self._transport,
                timeout=20.0,
                retries=2,
            )
        except (graphql.ValidationError, graphql.ExtractionError) as error:
            raise RewstReadError(_safe_error(error)) from error
        if not isinstance(data, dict):
            raise RewstReadError("Rewst returned an unexpected response shape")
        return {"data": data}


def _safe_error(error: Exception) -> str:
    message = str(error)
    if message.startswith(("HTTP 401", "HTTP 403")):
        return "Rewst authorization failed; renew the configured session and retry"
    if "GraphQL" in message or "result_path" in message:
        return "Rewst schema compatibility check failed; inspect a current read request"
    return "Rewst read failed; retry a bounded read after checking the connection"
