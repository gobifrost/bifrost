"""Shared SDK knowledge service (``shared.sdk_knowledge``).

The HTTP handlers (``/api/sdk/knowledge/*`` in ``api/src/routers/cli.py``)
delegate to this service. These tests pin the service contract and the
thin-adapter parity:

- one embedder and one repository across ``store_many``,
- commit ordering and failure rollback (one final commit; a failure
  before it rolls back prior flushed rows),
- exact-scope get/delete (repository built with the resolved org),
- 404 mapping on repository miss,
- search embeds the query exactly once and keeps the document shape,
- response shapes,
- external denial fires before any service call.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import shared.sdk_knowledge as svc
from shared.sdk_knowledge import (
    SDKKnowledgeError,
    delete_knowledge_document,
    delete_knowledge_namespace,
    get_knowledge_document,
    list_knowledge_namespaces,
    search_knowledge_documents,
    store_knowledge_document,
    store_many_knowledge_documents,
)
from src.core.auth import UserPrincipal
from src.models.contracts.cli import (
    CLIKnowledgeDeleteRequest,
    CLIKnowledgeSearchRequest,
    CLIKnowledgeStoreManyRequest,
    CLIKnowledgeStoreRequest,
)


def _session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class _FakeEmbedder:
    def __init__(self):
        self.embed_single_calls: list[str] = []

    async def embed_single(self, text):
        self.embed_single_calls.append(text)
        return [0.1, 0.2, 0.3]


def _patch_service(*, repo=None, embedder=None):
    """Patch the repository and embedding seams of the shared service."""
    repo_cls = repo if repo is not None else MagicMock()
    embedder_obj = embedder if embedder is not None else _FakeEmbedder()
    repo_patch = patch.object(
        svc.knowledge_repo_module, "KnowledgeRepository", repo_cls
    )
    embed_patch = patch.object(
        svc.embeddings_factory_module,
        "get_embedding_client",
        AsyncMock(return_value=embedder_obj),
    )
    return repo_patch, embed_patch, embedder_obj


def _doc(**overrides):
    base = {
        "id": str(uuid4()),
        "namespace": "default",
        "content": "hello world",
        "metadata": {"k": "v"},
        "score": 0.9,
        "organization_id": str(uuid4()),
        "key": "doc-key",
        "created_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _user(*, is_external=False):
    return UserPrincipal(
        user_id=uuid4(),
        email="sdk-knowledge@test.local",
        organization_id=uuid4(),
        is_superuser=False,
        is_external=is_external,
    )


@pytest.mark.asyncio
class TestStoreService:
    async def test_store_returns_first_chunk_id_and_commits(self):
        session = _session()
        org_id = uuid4()
        actor = uuid4()
        repo = AsyncMock()
        repo.store_chunked = AsyncMock(return_value=["chunk-0", "chunk-1"])
        repo_patch, embed_patch, embedder = _patch_service(
            repo=MagicMock(return_value=repo)
        )

        with repo_patch, embed_patch:
            result = await store_knowledge_document(
                session,
                content="hello",
                namespace="ns",
                key="k",
                metadata={"a": 1},
                org_id=org_id,
                created_by=actor,
            )

        assert result == {"id": "chunk-0"}
        _, kwargs = repo.store_chunked.await_args
        assert kwargs == {
            "content": "hello",
            "namespace": "ns",
            "key": "k",
            "metadata": {"a": 1},
            "created_by": actor,
            "embedder": embedder,
        }
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_store_builds_repo_in_exact_scope(self):
        session = _session()
        org_id = uuid4()
        seen = {}
        real_repo_class = MagicMock()

        def _factory(db, org_id=None, **kwargs):
            seen["org_id"] = org_id
            repo = AsyncMock()
            repo.store_chunked = AsyncMock(return_value=["chunk-0"])
            return repo

        real_repo_class.side_effect = _factory
        repo_patch, embed_patch, _ = _patch_service(repo=real_repo_class)

        with repo_patch, embed_patch:
            await store_knowledge_document(
                session, content="x", org_id=org_id, created_by=uuid4()
            )

        assert seen["org_id"] == org_id

    async def test_store_value_error_maps_to_503_and_rolls_back(self):
        session = _session()
        repo_patch, embed_patch, _ = _patch_service(
            repo=MagicMock(return_value=AsyncMock())
        )
        with (
            repo_patch,
            patch.object(
                svc.embeddings_factory_module,
                "get_embedding_client",
                AsyncMock(side_effect=ValueError("no embedding config")),
            ),
        ):
            with pytest.raises(SDKKnowledgeError) as exc:
                await store_knowledge_document(
                    session, content="x", org_id=uuid4(), created_by=uuid4()
                )
        assert exc.value.status_code == 503
        assert exc.value.detail == "no embedding config"
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()

    async def test_store_generic_error_maps_to_500_and_rolls_back(self):
        session = _session()
        repo = AsyncMock()
        repo.store_chunked = AsyncMock(side_effect=RuntimeError("db blew up"))
        repo_patch, embed_patch, _ = _patch_service(repo=MagicMock(return_value=repo))

        with repo_patch, embed_patch:
            with pytest.raises(SDKKnowledgeError) as exc:
                await store_knowledge_document(
                    session, content="x", org_id=uuid4(), created_by=uuid4()
                )
        assert exc.value.status_code == 500
        assert exc.value.detail == "Knowledge store failed: db blew up"
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()


@pytest.mark.asyncio
class TestStoreManyService:
    async def test_store_many_one_embedder_one_repo_one_commit(self):
        session = _session()
        org_id = uuid4()
        actor = uuid4()
        embedder = _FakeEmbedder()
        factory = AsyncMock(return_value=embedder)

        constructions: list = []
        repo = AsyncMock()
        repo.store_chunked = AsyncMock(side_effect=[["id-a"], ["id-b"]])

        def _repo_factory(db, org_id=None, **kwargs):
            constructions.append(org_id)
            return repo

        with (
            patch.object(
                svc.knowledge_repo_module,
                "KnowledgeRepository",
                MagicMock(side_effect=_repo_factory),
            ),
            patch.object(
                svc.embeddings_factory_module, "get_embedding_client", factory
            ),
        ):
            result = await store_many_knowledge_documents(
                session,
                documents=[
                    {"content": "one", "key": "k1"},
                    {"content": "two", "metadata": {"m": 1}},
                ],
                namespace="ns",
                org_id=org_id,
                created_by=actor,
            )

        assert result == {"ids": ["id-a", "id-b"]}
        factory.assert_awaited_once()
        assert constructions == [org_id]
        assert repo.store_chunked.await_count == 2
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_store_many_failure_rolls_back_without_commit(self):
        session = _session()
        repo = AsyncMock()
        repo.store_chunked = AsyncMock(
            side_effect=[["id-a"], RuntimeError("second doc failed")]
        )
        repo_patch, embed_patch, _ = _patch_service(repo=MagicMock(return_value=repo))

        with repo_patch, embed_patch:
            with pytest.raises(SDKKnowledgeError) as exc:
                await store_many_knowledge_documents(
                    session,
                    documents=[{"content": "one"}, {"content": "two"}],
                    namespace="ns",
                    org_id=uuid4(),
                    created_by=uuid4(),
                )

        assert exc.value.status_code == 500
        assert exc.value.detail == "Knowledge store failed: second doc failed"
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()

    async def test_store_many_missing_content_keeps_generic_failure(self):
        """No new DTO validation: a doc without content is the generic 500."""
        session = _session()
        repo = AsyncMock()
        repo.store_chunked = AsyncMock(return_value=["id-a"])
        repo_patch, embed_patch, _ = _patch_service(repo=MagicMock(return_value=repo))

        with repo_patch, embed_patch:
            with pytest.raises(SDKKnowledgeError) as exc:
                await store_many_knowledge_documents(
                    session,
                    documents=[{"content": "one"}, {"key": "no-content"}],
                    namespace="ns",
                    org_id=uuid4(),
                    created_by=uuid4(),
                )

        assert exc.value.status_code == 500
        assert exc.value.detail.startswith("Knowledge store failed:")
        # First doc was flushed, nothing committed.
        assert repo.store_chunked.await_count == 1
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()


@pytest.mark.asyncio
class TestSearchService:
    async def test_search_embeds_once_and_keeps_shape_without_commit(self):
        session = _session()
        org_id = uuid4()
        created = datetime.now(timezone.utc)
        repo = AsyncMock()
        repo.search = AsyncMock(
            return_value=[_doc(score=0.77, created_at=created)]
        )
        repo_patch, embed_patch, embedder = _patch_service(
            repo=MagicMock(return_value=repo)
        )

        with repo_patch, embed_patch:
            items = await search_knowledge_documents(
                session,
                query="my query",
                namespace=["default"],
                limit=5,
                min_score=None,
                metadata_filter=None,
                fallback=True,
                org_id=org_id,
            )

        assert embedder.embed_single_calls == ["my query"]
        _, kwargs = repo.search.await_args
        assert kwargs["query_text"] == "my query"
        assert kwargs["namespace"] == ["default"]
        assert kwargs["limit"] == 5
        assert kwargs["fallback"] is True
        assert kwargs["query_embedding"] == [0.1, 0.2, 0.3]
        assert items[0] == {
            "id": items[0]["id"],
            "namespace": "default",
            "content": "hello world",
            "metadata": {"k": "v"},
            "score": 0.77,
            "organization_id": items[0]["organization_id"],
            "key": "doc-key",
            "created_at": created.isoformat(),
        }
        assert set(items[0]) == {
            "id",
            "namespace",
            "content",
            "metadata",
            "score",
            "organization_id",
            "key",
            "created_at",
        }
        session.commit.assert_not_awaited()

    async def test_search_value_error_maps_to_503(self):
        session = _session()
        with patch.object(
            svc.embeddings_factory_module,
            "get_embedding_client",
            AsyncMock(side_effect=ValueError("no embedding config")),
        ):
            with pytest.raises(SDKKnowledgeError) as exc:
                await search_knowledge_documents(
                    session, query="q", namespace=["default"], org_id=uuid4()
                )
        assert exc.value.status_code == 503
        assert exc.value.detail == "no embedding config"

    async def test_search_generic_error_maps_to_500(self):
        session = _session()
        repo = AsyncMock()
        repo.search = AsyncMock(side_effect=RuntimeError("search down"))
        repo_patch, embed_patch, _ = _patch_service(repo=MagicMock(return_value=repo))

        with repo_patch, embed_patch:
            with pytest.raises(SDKKnowledgeError) as exc:
                await search_knowledge_documents(
                    session, query="q", namespace=["default"], org_id=uuid4()
                )
        assert exc.value.status_code == 500
        assert exc.value.detail == "Knowledge search failed: search down"


@pytest.mark.asyncio
class TestDeleteServices:
    async def test_delete_exact_scope_commits_and_returns_flag(self):
        session = _session()
        org_id = uuid4()
        seen = {}
        repo = AsyncMock()
        repo.delete_by_key = AsyncMock(return_value=True)

        def _factory(db, org_id=None, **kwargs):
            seen["org_id"] = org_id
            return repo

        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(side_effect=_factory),
        ):
            assert await delete_knowledge_document(
                session, key="k", namespace="ns", org_id=org_id
            ) == {"deleted": True}

        assert seen["org_id"] == org_id
        repo.delete_by_key.assert_awaited_once_with(key="k", namespace="ns")
        session.commit.assert_awaited_once()

    async def test_delete_missing_returns_false(self):
        session = _session()
        repo = AsyncMock()
        repo.delete_by_key = AsyncMock(return_value=False)
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            assert await delete_knowledge_document(
                session, key="absent", namespace="ns", org_id=uuid4()
            ) == {"deleted": False}

    async def test_delete_failure_maps_to_500_and_rolls_back(self):
        session = _session()
        repo = AsyncMock()
        repo.delete_by_key = AsyncMock(side_effect=RuntimeError("nope"))
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            with pytest.raises(SDKKnowledgeError) as exc:
                await delete_knowledge_document(
                    session, key="k", namespace="ns", org_id=uuid4()
                )
        assert exc.value.status_code == 500
        assert exc.value.detail == "Knowledge delete failed: nope"
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()

    async def test_delete_namespace_commits_count(self):
        session = _session()
        org_id = uuid4()
        repo = AsyncMock()
        repo.delete_namespace = AsyncMock(return_value=3)
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            assert await delete_knowledge_namespace(
                session, namespace="ns", org_id=org_id
            ) == {"deleted_count": 3}
        repo.delete_namespace.assert_awaited_once_with(namespace="ns")
        session.commit.assert_awaited_once()

    async def test_delete_namespace_failure_maps_to_500(self):
        session = _session()
        repo = AsyncMock()
        repo.delete_namespace = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            with pytest.raises(SDKKnowledgeError) as exc:
                await delete_knowledge_namespace(
                    session, namespace="ns", org_id=uuid4()
                )
        assert exc.value.status_code == 500
        assert exc.value.detail == "Knowledge delete namespace failed: boom"
        session.rollback.assert_awaited_once()


@pytest.mark.asyncio
class TestListAndGetServices:
    async def test_list_namespaces_shape_and_flag_no_commit(self):
        session = _session()
        repo = AsyncMock()
        repo.list_namespaces = AsyncMock(
            return_value=[SimpleNamespace(namespace="ns", scopes={"org": 2})]
        )
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            items = await list_knowledge_namespaces(
                session, org_id=uuid4(), include_global=False
            )
        assert items == [{"namespace": "ns", "scopes": {"org": 2}}]
        repo.list_namespaces.assert_awaited_once_with(include_global=False)
        session.commit.assert_not_awaited()

    async def test_list_namespaces_failure_maps_to_500(self):
        session = _session()
        repo = AsyncMock()
        repo.list_namespaces = AsyncMock(side_effect=RuntimeError("bad"))
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            with pytest.raises(SDKKnowledgeError) as exc:
                await list_knowledge_namespaces(session, org_id=uuid4())
        assert exc.value.status_code == 500
        assert exc.value.detail == "Knowledge list namespaces failed: bad"

    async def test_get_exact_scope_reassembled_shape(self):
        session = _session()
        org_id = uuid4()
        seen = {}
        created = datetime.now(timezone.utc)
        repo = AsyncMock()
        repo.get_by_key = AsyncMock(
            return_value=_doc(content="full content", created_at=created)
        )

        def _factory(db, org_id=None, **kwargs):
            seen["org_id"] = org_id
            return repo

        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(side_effect=_factory),
        ):
            item = await get_knowledge_document(
                session, key="k", namespace="ns", org_id=org_id
            )

        assert seen["org_id"] == org_id
        repo.get_by_key.assert_awaited_once_with(key="k", namespace="ns")
        assert item["content"] == "full content"
        assert item["created_at"] == created.isoformat()
        assert set(item) == {
            "id",
            "namespace",
            "content",
            "metadata",
            "organization_id",
            "key",
            "created_at",
        }
        session.commit.assert_not_awaited()

    async def test_get_miss_is_404(self):
        session = _session()
        repo = AsyncMock()
        repo.get_by_key = AsyncMock(return_value=None)
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ):
            with pytest.raises(SDKKnowledgeError) as exc:
                await get_knowledge_document(
                    session, key="absent", namespace="ns", org_id=uuid4()
                )
        assert exc.value.status_code == 404
        assert exc.value.detail == "Document not found"


@pytest.mark.asyncio
class TestHandlerAdapters:
    async def test_store_handler_passes_scalars_and_returns_id(self):
        from src.routers.cli import cli_knowledge_store

        user = _user()
        session = _session()
        real_store = svc.store_knowledge_document
        with patch.object(
            svc, "store_knowledge_document", wraps=real_store
        ) as spy, patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(
                return_value=AsyncMock(
                    store_chunked=AsyncMock(return_value=["chunk-0"])
                )
            ),
        ), patch.object(
            svc.embeddings_factory_module,
            "get_embedding_client",
            AsyncMock(return_value=_FakeEmbedder()),
        ):
            result = await cli_knowledge_store(
                CLIKnowledgeStoreRequest(content="hi", namespace="ns", key="k"),
                user,
                session,
            )
        assert result == {"id": "chunk-0"}
        spy.assert_awaited_once()
        _, kwargs = spy.call_args
        assert kwargs == {
            "content": "hi",
            "namespace": "ns",
            "key": "k",
            "metadata": None,
            "org_id": user.organization_id,
            "created_by": user.user_id,
        }

    async def test_search_handler_wraps_dicts_in_dtos(self):
        from src.routers.cli import cli_knowledge_search

        user = _user()
        session = _session()
        created = datetime.now(timezone.utc)
        repo = AsyncMock()
        repo.search = AsyncMock(
            return_value=[_doc(score=0.5, created_at=created)]
        )
        with patch.object(
            svc.knowledge_repo_module,
            "KnowledgeRepository",
            MagicMock(return_value=repo),
        ), patch.object(
            svc.embeddings_factory_module,
            "get_embedding_client",
            AsyncMock(return_value=_FakeEmbedder()),
        ):
            items = await cli_knowledge_search(
                CLIKnowledgeSearchRequest(query="q"), user, session
            )
        assert len(items) == 1
        assert items[0].content == "hello world"
        assert items[0].score == 0.5
        assert items[0].created_at == created.isoformat()

    async def test_get_handler_miss_maps_to_404(self):
        from src.routers.cli import cli_knowledge_get

        user = _user()
        session = _session()
        with patch.object(
            svc,
            "get_knowledge_document",
            AsyncMock(side_effect=SDKKnowledgeError(404, "Document not found")),
        ):
            with pytest.raises(HTTPException) as exc:
                await cli_knowledge_get("absent", "ns", None, user, session)
        assert exc.value.status_code == 404
        assert exc.value.detail == "Document not found"

    async def test_store_many_handler_error_detail_preserved(self):
        from src.routers.cli import cli_knowledge_store_many

        user = _user()
        session = _session()
        with patch.object(
            svc,
            "store_many_knowledge_documents",
            AsyncMock(side_effect=SDKKnowledgeError(503, "no embedding config")),
        ):
            with pytest.raises(HTTPException) as exc:
                await cli_knowledge_store_many(
                    CLIKnowledgeStoreManyRequest(documents=[{"content": "x"}]),
                    user,
                    session,
                )
        assert exc.value.status_code == 503
        assert exc.value.detail == "no embedding config"

    async def test_external_denial_before_any_service_call(self):
        import src.routers.cli as cli_module

        user = _user(is_external=True)
        session = _session()
        cases = [
            (
                "store_knowledge_document",
                lambda: cli_module.cli_knowledge_store(
                    CLIKnowledgeStoreRequest(content="x"), user, session
                ),
            ),
            (
                "store_many_knowledge_documents",
                lambda: cli_module.cli_knowledge_store_many(
                    CLIKnowledgeStoreManyRequest(documents=[{"content": "x"}]),
                    user,
                    session,
                ),
            ),
            (
                "search_knowledge_documents",
                lambda: cli_module.cli_knowledge_search(
                    CLIKnowledgeSearchRequest(query="q"), user, session
                ),
            ),
            (
                "delete_knowledge_document",
                lambda: cli_module.cli_knowledge_delete(
                    CLIKnowledgeDeleteRequest(key="k"), user, session
                ),
            ),
            (
                "delete_knowledge_namespace",
                lambda: cli_module.cli_knowledge_delete_namespace(
                    "ns", None, user, session
                ),
            ),
            (
                "list_knowledge_namespaces",
                lambda: cli_module.cli_knowledge_list_namespaces(
                    None, True, user, session
                ),
            ),
            (
                "get_knowledge_document",
                lambda: cli_module.cli_knowledge_get("k", "ns", None, user, session),
            ),
        ]
        for attr, call in cases:
            with patch.object(
                svc, attr, AsyncMock(return_value={})
            ) as service_mock:
                with pytest.raises(HTTPException) as exc:
                    await call()
                assert exc.value.status_code == 403, attr
                assert exc.value.detail == (
                    "External users cannot access the knowledge store directly"
                ), attr
                service_mock.assert_not_awaited()
        session.execute.assert_not_awaited()
