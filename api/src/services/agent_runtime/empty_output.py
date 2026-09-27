"""Circuit breaker for blank / repetitive no-tool model output.

Evidence: autonomous runs where the provider billed exactly 131072 output
tokens with zero visible content and zero tool calls (tickets 434487, 433859,
433906, 433915, 433917, 433218, ~$0.037 each), plus a 527K-token repetitive
no-tool completion (~$0.38) and a 132K-token duplicate-run completion (~$0.12).

Why this layer exists
---------------------
Pydantic AI (2.35.3, ``end_strategy="exhaustive"``, ``retries=1``) treats an
empty model response — no text parts and no tool calls — as a valid final
output. There is no built-in empty-response guard; the documented rejection
path is raising ``ModelRetry`` from ``after_model_request``, which appends the
rejected response to history and asks the model to try again (charging the
retry to the run's existing ``UsageLimits`` ledger). Other harnesses handle
the same failure differently: the OpenAI Agents SDK leans on
guardrails/output-validators that reject weak output before continuation, and
LangChain leans on output parsers with a bounded retry plus a fallback value.
This capability is Bifrost's equivalent: reject once via ``ModelRetry`` with a
tightened output cap, then stop retrying and hand a durable handoff to the
run owner instead of leaving ownership stalled or looping.

Why the cap was exactly 131072
------------------------------
Bifrost resolves a per-request ``max_tokens`` via ``request_max_tokens``:
explicit agent ``llm_max_tokens`` wins, then the model profile's
``default_max_tokens``, then Anthropic's required 16384 / the DeepSeek-family
8000 guard, then the provider default (None) — see
``agent_model_settings``. Before any fallback existed, runs with no explicit
cap sent nothing and the provider fell back to its own model default, which
for the affected runs was 131072 (128 KiB tokens). The bounded fallback
below therefore sets an explicit small ``max_tokens`` on the single retry
so a second blank completion cannot cost another 131072 tokens.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass, field, replace

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

EMPTY_OUTPUT_FALLBACK_MAX_TOKENS = 4_000
"""Explicit output cap for the single bounded retry after a blank response."""

EMPTY_OUTPUT_MAX_FALLBACKS = 1
"""Only one ModelRetry fallback is allowed; the next blank repeats hand off."""

REPETITIVE_MIN_CHARS = 2_000
"""Only completions above this size are eligible for repetition detection."""

REPETITIVE_MIN_SENTENCES = 10
"""Minimum sentence count before sentence-echo detection applies."""

REPETITIVE_MAX_UNIQUE_SENTENCE_RATIO = 0.25
"""Flag when fewer than this fraction of sentences are unique (echo)."""

REPETITIVE_MAX_COMPRESSED_RATIO = 0.05
"""Flag boundary-less echo via zlib size ratio (e.g. one repeated token)."""

_EMPTY_HANDOFF_REASON = "empty_model_output"


def _normalize_text(text: str | None) -> str:
    """Collapse whitespace for blank / repetition checks."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().casefold()


def is_blank_text(text: str | None) -> bool:
    """Return True for None, empty, or whitespace-only model text."""
    return not _normalize_text(text)


def is_empty_no_tool_response(response: ModelResponse) -> bool:
    """Return True when a response carries no content and no tool calls."""
    return is_blank_text(response.text) and not response.tool_calls


def response_fingerprint(response: ModelResponse) -> str:
    """Stable fingerprint of visible text used for repetition detection."""
    return hashlib.sha256(_normalize_text(response.text).encode("utf-8")).hexdigest()


def is_internally_repetitive(text: str | None) -> bool:
    """Return True when long text is mostly the same sentence on repeat.

    Catches the 527K-token incident shape: a single no-tool completion that
    echoes itself (high output tokens, near-zero unique content). Sentence
    splitting is alignment-free, unlike fixed-width chunking, and short
    answers never qualify, so ordinary prose is unaffected. A zlib ratio
    catches boundary-less echo (one repeated token with no punctuation).
    """
    normalized = _normalize_text(text)
    if len(normalized) < REPETITIVE_MIN_CHARS:
        return False
    sentences = [
        sentence
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", normalized)
        if sentence
    ]
    if (
        len(sentences) >= REPETITIVE_MIN_SENTENCES
        and len(set(sentences)) / len(sentences)
        < REPETITIVE_MAX_UNIQUE_SENTENCE_RATIO
    ):
        return True
    compressed_ratio = len(zlib.compress(normalized.encode("utf-8"))) / len(
        normalized
    )
    return compressed_ratio < REPETITIVE_MAX_COMPRESSED_RATIO


def empty_output_handoff_text(*, repeated: bool) -> str:
    """Durable handoff surfaced as the run's final output after the fallback."""
    kind = (
        "repeated its previous answer without calling a tool"
        if repeated
        else "returned an empty response without calling a tool"
    )
    return (
        "I stopped here because the model "
        f"{kind} after one bounded retry, so continuing would only burn "
        "budget without progress. Completed tool results and run steps are "
        "preserved in this run. A human should review the goal and either "
        "retry with narrower instructions or continue manually — no further "
        "automatic model attempts were made."
    )


@dataclass
class EmptyOutputCircuitBreaker(AbstractCapability[object]):
    """Reject blank / repetitive no-tool completions once, then hand off.

    - First blank or repeated no-tool response: raise ``ModelRetry`` (the
      rejected response stays in history and its usage is already charged to
      the shared ``UsageLimits`` ledger by ``ObservedModel``).
    - The retry request is capped to ``EMPTY_OUTPUT_FALLBACK_MAX_TOKENS`` via
      ``before_model_request`` so a second blank completion is bounded.
    - Second consecutive blank / repeat: return a synthetic stop response
      with handoff text instead of retrying, and set ``handoff_triggered``
      so the executor can record a durable ``budget_warning`` step.
    """

    max_fallbacks: int = EMPTY_OUTPUT_MAX_FALLBACKS
    fallback_max_tokens: int = EMPTY_OUTPUT_FALLBACK_MAX_TOKENS
    _fallbacks_used: int = field(default=0, init=False, repr=False)
    _fallback_armed: bool = field(default=False, init=False, repr=False)
    _consecutive_empty: int = field(default=0, init=False, repr=False)
    _seen_fingerprints: set[str] = field(default_factory=set, init=False, repr=False)
    handoff_triggered: bool = field(default=False, init=False)
    handoff_reason: str = field(default=_EMPTY_HANDOFF_REASON, init=False)

    @property
    def fallbacks_used(self) -> int:
        """Number of ModelRetry fallbacks consumed so far this run."""
        return self._fallbacks_used

    @property
    def saw_empty_response(self) -> bool:
        """True once any blank no-tool response was rejected this run."""
        return self._consecutive_empty > 0 or self.handoff_triggered

    def _is_repetitive(self, response: ModelResponse) -> bool:
        """True for progress-free text that echoes itself or a prior answer.

        Two incident shapes: (a) one completion that repeats internally
        (527K tokens of echo, billed once), and (b) a no-tool completion whose
        visible text duplicates an earlier one in this run. Responses with
        tool calls are never flagged — side effects are in flight.
        """
        if response.tool_calls or is_blank_text(response.text):
            return False
        if is_internally_repetitive(response.text):
            return True
        return response_fingerprint(response) in self._seen_fingerprints

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        if not self._fallback_armed:
            return request_context
        settings = dict(request_context.model_settings or {})
        current = settings.get("max_tokens")
        if not isinstance(current, int) or current > self.fallback_max_tokens:
            settings["max_tokens"] = self.fallback_max_tokens
        return replace(request_context, model_settings=settings)  # type: ignore[typeddict-item]

    async def after_model_request(
        self,
        ctx: RunContext[object],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        del ctx, request_context
        empty = is_empty_no_tool_response(response)
        repeated = self._is_repetitive(response)

        if not empty and not repeated:
            self._consecutive_empty = 0
            if response.text and not response.tool_calls:
                self._seen_fingerprints.add(response_fingerprint(response))
            return response

        if empty:
            self._consecutive_empty += 1
        else:
            # A repeated no-tool completion is progress-free even when the
            # first copy looked fine; treat it like a blank for budgeting.
            self._consecutive_empty += 1

        if self._fallbacks_used < self.max_fallbacks:
            self._fallbacks_used += 1
            self._fallback_armed = True
            if empty:
                raise ModelRetry(
                    "Your previous response was empty: no text and no tool "
                    "call. Reply now with concise text that answers the task, "
                    "or call exactly one tool that moves the task forward."
                )
            raise ModelRetry(
                "Your previous response repeated an earlier answer without "
                "calling a tool. Provide a novel next step: new text or "
                "exactly one tool call that moves the task forward."
            )

        self._fallback_armed = False
        self.handoff_triggered = True
        handoff = empty_output_handoff_text(repeated=not empty)
        parts: list = [TextPart(content=handoff)]
        return replace(response, parts=parts, finish_reason="stop")
