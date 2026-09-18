"""Wire-level regression tests for the compact knowledge-search default.

Finding ``knowledge-search-result-overfetch`` (occ 29): full document content
was returned by default (e.g. ticket 434459 — two searches, 20633 chars /
~5159 tokens into the worker context). The compact default returns ranked id
+ title + confidence + bounded excerpt and requires an explicit follow-up for
full content, keeping the 40K envelope as a ceiling instead of the norm.
"""

import json

import pytest

from src.services.knowledge.search_budget import (
    KNOWLEDGE_DEFAULT_EXCERPT_CHARS,
    MAX_KNOWLEDGE_EVIDENCE_CHARS_PER_TURN,
    build_compact_knowledge_document,
    compact_knowledge_excerpt,
    compact_knowledge_metadata,
    parse_knowledge_followup,
)


def _legacy_full_document(index: int, content: str) -> dict:
    """The pre-compact wire shape: full content embedded per result."""
    return {
        "content": content,
        "namespace": "halo_kb",
        "score": 0.9,
        "key": f"doc-{index}",
        "metadata": {"title": f"Doc {index}"},
    }


def _wire_chars(documents: list[dict]) -> int:
    return len(json.dumps({"documents": documents}, ensure_ascii=False))


def test_compact_default_slashes_wire_payload_before_after():
    """Before/after measurement on ticket-434459-scale content (2x ~10K chars)."""
    contents = ["Contact configuration notes. " * 350 for _ in range(2)]
    assert sum(len(content) for content in contents) >= 20_000

    before_documents = [
        _legacy_full_document(index, content)
        for index, content in enumerate(contents)
    ]
    after_documents = [
        build_compact_knowledge_document(
            f"chunk-{index}",
            content=content,
            namespace="halo_kb",
            score=0.9,
            key=f"doc-{index}",
            metadata=compact_knowledge_metadata({"title": f"Doc {index}"}),
        )
        for index, content in enumerate(contents)
    ]

    before_chars = _wire_chars(before_documents)
    after_chars = _wire_chars(after_documents)
    # Rough token estimate (~4 chars/token) for the PR/measurement record.
    before_tokens = before_chars / 4
    after_tokens = after_chars / 4

    assert after_chars < before_chars * 0.15
    assert after_tokens < before_tokens * 0.15
    assert after_chars < 3_000  # bounded excerpts, not the 20K legacy blob
    for document in after_documents:
        assert "content" not in document  # full content withheld by default
        assert document["id"].startswith("chunk-")
        assert document["title"] == f"Doc {int(document['id'].rsplit('-', 1)[1])}"
        assert document["confidence"] == 0.9
        assert len(document["excerpt"]) <= KNOWLEDGE_DEFAULT_EXCERPT_CHARS + 200
        assert document["has_more_content"] is True
        assert document["full_chars"] == len(contents[0])


def test_five_large_docs_stay_small_by_default():
    """5 x 4K-char docs: legacy ~20K chars vs compact ~3-4K chars."""
    contents = ["x" * 4_000 for _ in range(5)]
    before_chars = _wire_chars(
        [_legacy_full_document(index, content) for index, content in enumerate(contents)]
    )
    after_chars = _wire_chars(
        [
            build_compact_knowledge_document(
                f"chunk-{index}",
                content=content,
                namespace="halo_kb",
                score=0.8,
                key=f"doc-{index}",
                metadata={"title": f"Doc {index}"},
            )
            for index, content in enumerate(contents)
        ]
    )
    assert before_chars > 20_000
    assert after_chars < 5_000
    assert after_chars <= MAX_KNOWLEDGE_EVIDENCE_CHARS_PER_TURN


def test_explicit_followup_returns_full_content():
    compact = build_compact_knowledge_document(
        "chunk-1",
        content="full body here",
        namespace="halo_kb",
        score=0.9,
        key="doc-1",
        metadata={"title": "Doc 1"},
    )
    full = build_compact_knowledge_document(
        "chunk-1",
        content="full body here",
        namespace="halo_kb",
        score=0.9,
        key="doc-1",
        metadata={"title": "Doc 1"},
        include_full=True,
    )
    assert "content" not in compact
    assert full["content"] == "full body here"
    # Compact shape still carries ranking fields in both modes.
    assert full["title"] == "Doc 1"
    assert full["confidence"] == 0.9


def test_short_content_has_no_truncation_marker():
    assert compact_knowledge_excerpt("short") == "short"
    excerpted = compact_knowledge_excerpt("y" * 600)
    assert len("y" * 600) - KNOWLEDGE_DEFAULT_EXCERPT_CHARS > 0
    assert "withheld" in excerpted


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, (None, False)),
        ({"query": "x"}, (None, False)),
        ({"doc_id": "chunk-1"}, ("chunk-1", False)),
        (
            {"doc_id": "chunk-1", "include_full_content": True},
            ("chunk-1", True),
        ),
        ({"include_full_content": True}, (None, True)),
        ({"include_full_content": "yes"}, (None, False)),
        ({"doc_id": "  "}, (None, False)),
    ],
)
def test_parse_knowledge_followup(arguments, expected):
    assert parse_knowledge_followup(arguments) == expected


class TestCompactExecutorRoundTrip:
    """The tool path serves excerpts by default and full content on follow-up."""

    @pytest.mark.asyncio
    async def test_search_then_full_content_followup(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        from src.repositories.knowledge import KnowledgeDocument
        from src.services.execution.autonomous_agent_executor import (
            AutonomousAgentExecutor,
        )
        from src.services.llm.base import ToolCallRequest

        session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=mock_ctx)

        agent = MagicMock()
        agent.knowledge_sources = ["halo_kb"]
        agent.organization_id = uuid4()

        long_content = "Contact configuration notes. " * 200  # ~5.8K chars
        document = KnowledgeDocument(
            id="chunk-9",
            content=long_content,
            namespace="halo_kb",
            score=0.9123,
            key="contact-types",
            metadata={"title": "Contact Types"},
        )
        embedding_client = MagicMock()
        embedding_client.embed_single = AsyncMock(return_value=[0.1, 0.2])
        repository = MagicMock()
        repository.search = AsyncMock(return_value=[document])

        executor = AutonomousAgentExecutor(factory)
        with patch(
            "src.services.embeddings.get_embedding_client",
            new_callable=AsyncMock,
            return_value=embedding_client,
        ), patch(
            "src.repositories.knowledge.KnowledgeRepository",
            return_value=repository,
        ):
            first = json.loads(
                await executor._execute_knowledge_search(
                    ToolCallRequest(
                        id="first",
                        name="search_knowledge",
                        arguments={"query": "contact roles"},
                    ),
                    agent,
                )
            )
            assert first["count"] == 1
            assert first["compact"] is True
            compact_doc = first["documents"][0]
            assert compact_doc["id"] == "chunk-9"
            assert compact_doc["title"] == "Contact Types"
            assert compact_doc["confidence"] == 0.9123
            assert "content" not in compact_doc
            assert len(compact_doc["excerpt"]) < len(long_content)
            assert compact_doc["has_more_content"] is True

            followup = json.loads(
                await executor._execute_knowledge_search(
                    ToolCallRequest(
                        id="second",
                        name="search_knowledge",
                        arguments={
                            "doc_id": "chunk-9",
                            "include_full_content": True,
                        },
                    ),
                    agent,
                )
            )
            assert followup["count"] == 1
            assert followup["from_cache"] is True
            assert followup["documents"][0]["content"] == long_content
            # The cache follow-up skips embedding + repository search.
            assert embedding_client.embed_single.await_count == 1
            assert repository.search.await_count == 1

            unknown = json.loads(
                await executor._execute_knowledge_search(
                    ToolCallRequest(
                        id="third",
                        name="search_knowledge",
                        arguments={"doc_id": "nope", "include_full_content": True},
                    ),
                    agent,
                )
            )
            assert unknown["count"] == 0
