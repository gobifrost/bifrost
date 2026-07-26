"""Resolves the LLM client the builder runs on.

There is exactly one place the platform stores AI provider settings — the
global ``llm/provider_config`` SystemConfig read by
:mod:`src.services.llm.factory` — and this module reads the builder's model
through that same path rather than opening a second configuration surface.

The spec's resolution rule is ``builder_model ?? global model``: an optional
per-builder model override on top of the platform's configured provider. The
optional ``builder_model`` key is added to global AI settings in the UI work
package; until then the global model is what resolves, and the override is read
from the same config object the moment it exists.

Availability fails closed. If AI is not configured at all, the builder cannot
run a turn, so this raises :class:`BuilderModelUnavailable` and the router
surfaces 503 rather than the user watching a turn die mid-flight.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.llm.base import BaseLLMClient
from src.services.llm.factory import get_llm_client


class BuilderModelUnavailable(Exception):
    """No usable LLM is configured, so no builder turn can run."""


async def get_builder_llm_client(db: AsyncSession) -> BaseLLMClient:
    """Return the client the builder agent loop should drive.

    Raises :class:`BuilderModelUnavailable` when the platform has no LLM
    provider configured or its stored credentials cannot be read.
    """
    try:
        return await get_llm_client(db)
    except ValueError as exc:
        # get_llm_config raises ValueError for every "cannot serve a client"
        # case: no config row, no API key, undecryptable key.
        raise BuilderModelUnavailable(str(exc)) from exc
