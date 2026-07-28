"""Per-turn evidence controls for agent knowledge retrieval.

Knowledge results are fed back into the model's conversation history. Repeating
the same search (or issuing many searches in one tool-calling loop) therefore
causes every later LLM request to resend all earlier results. The primary bound
is therefore the serialized evidence returned during a turn, not a low query
count. Exact-query and query-count guards remain as loop protection.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal


MAX_KNOWLEDGE_RESULTS = 5
MAX_KNOWLEDGE_SEARCHES_PER_TURN = 8
MAX_KNOWLEDGE_EVIDENCE_CHARS_PER_TURN = 40_000
MIN_KNOWLEDGE_EVIDENCE_CHARS = 512
MAX_KNOWLEDGE_METADATA_CHARS = 2_000

_KNOWLEDGE_METADATA_PRIORITY = (
    "title",
    "source",
    "source_url",
    "url",
    "parent_slug",
    "faq_breadcrumbs",
    "modified",
    "wp_id",
    "chunk_index",
    "chunk_count",
)
_KNOWLEDGE_METADATA_OMIT = frozenset({"image_uuids"})

KnowledgeSearchRejection = Literal[
    "duplicate_query",
    "search_budget_exhausted",
    "evidence_budget_exhausted",
]
EvidenceClaim = Literal["accepted", "duplicate_evidence", "evidence_budget_exhausted"]


@dataclass(frozen=True)
class KnowledgeSearchDecision:
    """Result of reserving one knowledge search in the current turn."""

    allowed: bool
    normalized_query: str
    searches_used: int
    searches_remaining: int
    evidence_chars_used: int
    evidence_chars_remaining: int
    rejection: KnowledgeSearchRejection | None = None


@dataclass(frozen=True)
class KnowledgeEvidenceSelection:
    """Novel evidence selected from one retrieval result."""

    documents: list[dict[str, Any]]
    omitted_duplicates: int
    omitted_for_budget: int
    evidence_chars_used: int
    evidence_chars_remaining: int


class KnowledgeSearchBudget:
    """Track unique queries and serialized evidence for one agent turn."""

    def __init__(
        self,
        max_searches: int = MAX_KNOWLEDGE_SEARCHES_PER_TURN,
        max_evidence_chars: int = MAX_KNOWLEDGE_EVIDENCE_CHARS_PER_TURN,
    ):
        if max_searches < 1:
            raise ValueError("max_searches must be at least 1")
        if max_evidence_chars < MIN_KNOWLEDGE_EVIDENCE_CHARS:
            raise ValueError(
                "max_evidence_chars must be at least "
                f"{MIN_KNOWLEDGE_EVIDENCE_CHARS}"
            )
        self.max_searches = max_searches
        self.max_evidence_chars = max_evidence_chars
        self._queries: set[str] = set()
        self._evidence_ids: set[str] = set()
        self._evidence_chars = 0

    @property
    def searches_used(self) -> int:
        return len(self._queries)

    @property
    def searches_remaining(self) -> int:
        return max(0, self.max_searches - self.searches_used)

    @property
    def evidence_chars_used(self) -> int:
        return self._evidence_chars

    @property
    def evidence_chars_remaining(self) -> int:
        return max(0, self.max_evidence_chars - self.evidence_chars_used)

    @property
    def exhausted(self) -> bool:
        return (
            self.searches_remaining == 0
            or self.evidence_chars_remaining < MIN_KNOWLEDGE_EVIDENCE_CHARS
        )

    def reset(self) -> None:
        """Start a fresh agent turn."""
        self._queries.clear()
        self._evidence_ids.clear()
        self._evidence_chars = 0

    def reserve(self, query: str) -> KnowledgeSearchDecision:
        """Reserve a unique query or explain why it should not execute."""
        normalized = " ".join(query.split()).casefold()

        if normalized in self._queries:
            return KnowledgeSearchDecision(
                allowed=False,
                normalized_query=normalized,
                searches_used=self.searches_used,
                searches_remaining=self.searches_remaining,
                evidence_chars_used=self.evidence_chars_used,
                evidence_chars_remaining=self.evidence_chars_remaining,
                rejection="duplicate_query",
            )

        if self.searches_remaining == 0:
            return KnowledgeSearchDecision(
                allowed=False,
                normalized_query=normalized,
                searches_used=self.searches_used,
                searches_remaining=0,
                evidence_chars_used=self.evidence_chars_used,
                evidence_chars_remaining=self.evidence_chars_remaining,
                rejection="search_budget_exhausted",
            )

        if self.evidence_chars_remaining < MIN_KNOWLEDGE_EVIDENCE_CHARS:
            return KnowledgeSearchDecision(
                allowed=False,
                normalized_query=normalized,
                searches_used=self.searches_used,
                searches_remaining=self.searches_remaining,
                evidence_chars_used=self.evidence_chars_used,
                evidence_chars_remaining=self.evidence_chars_remaining,
                rejection="evidence_budget_exhausted",
            )

        self._queries.add(normalized)
        return KnowledgeSearchDecision(
            allowed=True,
            normalized_query=normalized,
            searches_used=self.searches_used,
            searches_remaining=self.searches_remaining,
            evidence_chars_used=self.evidence_chars_used,
            evidence_chars_remaining=self.evidence_chars_remaining,
        )

    def claim_evidence(self, evidence_id: str, serialized_chars: int) -> EvidenceClaim:
        """Reserve one serialized chunk if it is novel and fits the envelope."""
        if serialized_chars < 0:
            raise ValueError("serialized_chars cannot be negative")
        if evidence_id in self._evidence_ids:
            return "duplicate_evidence"
        if serialized_chars > self.evidence_chars_remaining:
            return "evidence_budget_exhausted"

        self._evidence_ids.add(evidence_id)
        self._evidence_chars += serialized_chars
        return "accepted"


def compact_knowledge_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep useful source metadata within a small, deterministic envelope.

    Agent evidence needs titles and source/navigation fields for grounding and
    citations. It does not need transport-only image UUID arrays, and arbitrary
    metadata must not be able to consume the entire turn evidence budget.
    Small custom fields are preserved after the common source fields.
    """
    ordered_keys = [
        key for key in _KNOWLEDGE_METADATA_PRIORITY if key in metadata
    ]
    ordered_keys.extend(
        sorted(
            key
            for key in metadata
            if key not in _KNOWLEDGE_METADATA_PRIORITY
            and key not in _KNOWLEDGE_METADATA_OMIT
        )
    )

    compacted: dict[str, Any] = {}
    for key in ordered_keys:
        if key in _KNOWLEDGE_METADATA_OMIT:
            continue
        candidate = {**compacted, key: metadata[key]}
        serialized_chars = len(
            json.dumps(
                candidate,
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if serialized_chars <= MAX_KNOWLEDGE_METADATA_CHARS:
            compacted[key] = metadata[key]

    return compacted


def select_novel_knowledge_evidence(
    budget: KnowledgeSearchBudget,
    documents: list[tuple[str, dict[str, Any]]],
) -> KnowledgeEvidenceSelection:
    """Deduplicate and budget the actual JSON payload sent to the model."""
    selected: list[dict[str, Any]] = []
    omitted_duplicates = 0
    omitted_for_budget = 0

    for evidence_id, document in documents:
        serialized_chars = len(
            json.dumps(
                document,
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        claim = budget.claim_evidence(evidence_id, serialized_chars)
        if claim == "accepted":
            selected.append(document)
        elif claim == "duplicate_evidence":
            omitted_duplicates += 1
        else:
            omitted_for_budget += 1

    return KnowledgeEvidenceSelection(
        documents=selected,
        omitted_duplicates=omitted_duplicates,
        omitted_for_budget=omitted_for_budget,
        evidence_chars_used=budget.evidence_chars_used,
        evidence_chars_remaining=budget.evidence_chars_remaining,
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
    elif decision.rejection == "evidence_budget_exhausted":
        message = (
            "The knowledge evidence envelope for this turn is full. Synthesize "
            "the answer from results already returned; do not search again."
        )
    else:
        message = (
            f"Knowledge search safety ceiling reached ({decision.searches_used} unique "
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
        "evidence_chars_used": decision.evidence_chars_used,
        "evidence_chars_remaining": decision.evidence_chars_remaining,
    }
