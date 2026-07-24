"""Per-turn limits for agent knowledge retrieval.

Knowledge results are fed back into the model's conversation history. Repeating
the same search (or issuing many searches in one tool-calling loop) therefore
causes every later LLM request to resend all earlier results. These guards keep
that cumulative context bounded while still allowing a few distinct retrieval
attempts for synonyms and product-specific vocabulary.
"""

from dataclasses import dataclass
from typing import Literal


MAX_KNOWLEDGE_RESULTS = 5
MAX_KNOWLEDGE_SEARCHES_PER_TURN = 3

KnowledgeSearchRejection = Literal["duplicate_query", "search_budget_exhausted"]


@dataclass(frozen=True)
class KnowledgeSearchDecision:
    """Result of reserving one knowledge search in the current turn."""

    allowed: bool
    normalized_query: str
    searches_used: int
    searches_remaining: int
    rejection: KnowledgeSearchRejection | None = None


class KnowledgeSearchBudget:
    """Track unique knowledge searches for one agent turn."""

    def __init__(self, max_searches: int = MAX_KNOWLEDGE_SEARCHES_PER_TURN):
        if max_searches < 1:
            raise ValueError("max_searches must be at least 1")
        self.max_searches = max_searches
        self._queries: set[str] = set()

    @property
    def searches_used(self) -> int:
        return len(self._queries)

    @property
    def searches_remaining(self) -> int:
        return max(0, self.max_searches - self.searches_used)

    @property
    def exhausted(self) -> bool:
        return self.searches_remaining == 0

    def reset(self) -> None:
        """Start a fresh agent turn."""
        self._queries.clear()

    def reserve(self, query: str) -> KnowledgeSearchDecision:
        """Reserve a unique query or explain why it should not execute."""
        normalized = " ".join(query.split()).casefold()

        if normalized in self._queries:
            return KnowledgeSearchDecision(
                allowed=False,
                normalized_query=normalized,
                searches_used=self.searches_used,
                searches_remaining=self.searches_remaining,
                rejection="duplicate_query",
            )

        if self.exhausted:
            return KnowledgeSearchDecision(
                allowed=False,
                normalized_query=normalized,
                searches_used=self.searches_used,
                searches_remaining=0,
                rejection="search_budget_exhausted",
            )

        self._queries.add(normalized)
        return KnowledgeSearchDecision(
            allowed=True,
            normalized_query=normalized,
            searches_used=self.searches_used,
            searches_remaining=self.searches_remaining,
        )


def clamp_knowledge_result_limit(value: object) -> int:
    """Coerce an LLM-supplied result limit into the supported 1..5 range."""
    try:
        requested = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        requested = MAX_KNOWLEDGE_RESULTS
    return max(1, min(requested, MAX_KNOWLEDGE_RESULTS))


def knowledge_search_rejection_payload(
    decision: KnowledgeSearchDecision,
) -> dict[str, object]:
    """Return a small tool payload that directs the model away from a loop."""
    if decision.rejection == "duplicate_query":
        message = (
            "This exact query was already searched in this turn. Reuse the "
            "previous results or try a materially different query."
        )
    else:
        message = (
            f"Knowledge search budget reached ({decision.searches_used} unique "
            "queries). Synthesize the answer from results already returned; "
            "do not search again."
        )

    return {
        "documents": [],
        "count": 0,
        "search_skipped": True,
        "reason": decision.rejection,
        "message": message,
        "searches_used": decision.searches_used,
        "searches_remaining": decision.searches_remaining,
    }
