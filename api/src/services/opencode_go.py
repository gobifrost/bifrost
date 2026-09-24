"""OpenCode Go gateway identity and required request headers.

OpenCode Go (https://opencode.ai/docs/go) is an OpenAI-compatible gateway
that serves its catalog across three wire surfaces:

- ``/chat/completions`` for most coding models,
- ``/responses`` for Grok, GPT Luna, and Muse Spark models,
- ``/messages`` (Anthropic Messages) for MiniMax and Qwen models.

Its docs also ask every client to identify itself with a client user agent and
to send a stable session id in ``x-opencode-session`` for each conversation so
the gateway can route requests and reuse prompt caches. Bifrost supplies both
on every OpenCode Go request and routes each model to its documented surface.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse, urlunparse

from shared.version import get_version

OPENCODE_GO_DEFAULT_ENDPOINT = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_SESSION_HEADER = "x-opencode-session"

#: Model-id prefixes served through the OpenAI Responses API.
OPENCODE_GO_RESPONSES_MODEL_PREFIXES = (
    "grok-4",
    "gpt-6-luna",
    "gpt-5.6-luna",
    "muse-spark",
)
#: Model-id prefixes served through the Anthropic Messages API.
OPENCODE_GO_MESSAGES_MODEL_PREFIXES = ("minimax-", "qwen3")

OpenCodeGoWireApi = Literal["chat_completions", "responses", "messages"]


def is_opencode_go_endpoint(endpoint: str | None) -> bool:
    """Return whether an OpenAI-compatible endpoint is the OpenCode Go gateway."""

    if not endpoint:
        return False
    parsed = urlparse(endpoint)
    hostname = parsed.hostname or ""
    if hostname != "opencode.ai" and not hostname.endswith(".opencode.ai"):
        return False
    return parsed.path.rstrip("/").startswith("/zen/go")


def opencode_go_wire_api(model: str) -> OpenCodeGoWireApi:
    """Return the wire surface OpenCode Go documents for a model id.

    Unknown models default to Chat Completions: that is the surface the
    gateway serves for the bulk of its catalog, including every model added
    since the documentation was last updated.
    """

    normalized = model.strip().lower()
    if normalized.startswith(OPENCODE_GO_RESPONSES_MODEL_PREFIXES):
        return "responses"
    if normalized.startswith(OPENCODE_GO_MESSAGES_MODEL_PREFIXES):
        return "messages"
    return "chat_completions"


def opencode_go_anthropic_endpoint(endpoint: str | None) -> str | None:
    """Return the Anthropic SDK base URL for the OpenCode Go Messages surface.

    The Anthropic SDK concatenates its ``/v1/messages`` path onto the base
    URL path, so the Go ``/v1`` suffix is dropped to reach
    ``https://opencode.ai/zen/go/v1/messages`` instead of a doubled ``/v1``.
    """

    if not endpoint:
        return endpoint
    parsed = urlparse(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunparse(parsed._replace(path=path))


def opencode_go_extra_headers(session_id: str | None = None) -> dict[str, str]:
    """Return the identity headers OpenCode Go requests must carry.

    The user agent names Bifrost instead of the underlying SDK, and the session
    id stays stable for one conversation so the gateway can keep routing and
    prompt-cache affinity. Session-less calls (model catalogs, probes) omit the
    session header deliberately rather than sending an empty value.
    """

    headers = {"User-Agent": f"Bifrost/{get_version()}"}
    if session_id:
        headers[OPENCODE_GO_SESSION_HEADER] = session_id
    return headers
