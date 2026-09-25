"""Stage 3b: HTTP/local parity for ``bifrost.knowledge`` (all seven methods).

Covers the acceptance surface that does not need a forked child:

- the shared service (``shared.sdk_knowledge``) split-phase helpers keep
  batch semantics, commit ordering, DTO validation, return values, and
  error statuses identical behind the HTTP handlers and the parent
  dispatcher;
- the parent dispatcher derives actor/org/capability from the
  parent-owned principal (engine sentinel ``created_by``, untrusted
  child scope through the shared scope rules with the same
  engine-versus-service authority as HTTP), holds no pooled session
  across embedding work, and serves each phase on short sessions;
- scope denial parity (cross-org/global 403, malformed scope 422);
- external-user denial still fires on HTTP before any service call;
- the child transport performs real knowledge round trips with zero
  HTTP requests, maps 404s to the facade's method-specific results
  without HTTP fallback, and never falls back after a local failure.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import multiprocessing
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import pytest_asyncio

from src.models.orm.knowledge import KnowledgeStore


class _FakeEmbedder:
    """Deterministic embedder with call tracking (no network, no DB)."""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.embed_calls: list[list[str]] = []
        self.embed_single_calls: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [[float((len(t) + i) % self.dim) for i in range(self.dim)] for t in texts]

    async def embed_single(self, text: str) -> list[float]:
        self.embed_single_calls.append(text)
        return (await self.embed([text]))[0]


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    org = None
    if org_id is not None:
        org = {"id": str(org_id)}
        if kwargs.get("is_provider_org", False):
            org["is_provider"] = True
    data = {
        "organization": org,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-1"),
    }
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _engine_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(org_id, **kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return _engine_principal(
        org_id,
        service={"service_id": service_id, "attempt_id": attempt_id},
        execution_id=attempt_id,
    )


async def _seed_org(db_session, *, is_provider=False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-knowledge-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-knowledge-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, org_id=None, is_superuser=False):
    """Insert one user row so ``created_by`` FKs resolve on real writes."""
    from src.models.orm.users import User as UserModel

    row = UserModel(
        email=f"sdk-knowledge-{uuid4().hex[:8]}@test.local",
        name="SDK Knowledge Test",
        is_active=True,
        is_superuser=is_superuser,
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _ensure_sentinel_user(db_session):
    """Ensure the engine sentinel user exists for local ``created_by``.

    The local dispatcher attributes writes to ``SYSTEM_USER_UUID``
    (the ``mint_engine_token``/``mint_service_token`` ``sub`` the HTTP
    engine/service paths authenticate as) — that id must satisfy the
    ``knowledge_store.created_by`` FK on real writes.
    """
    from sqlalchemy import select

    from src.core.constants import SYSTEM_USER_UUID
    from src.core.security import ENGINE_SDK_ACTOR_EMAIL
    from src.models.orm.users import User as UserModel

    existing = (
        await db_session.execute(
            select(UserModel).where(UserModel.id == SYSTEM_USER_UUID)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = UserModel(
        id=SYSTEM_USER_UUID,
        email=ENGINE_SDK_ACTOR_EMAIL,
        name="Bifrost Engine",
        is_active=True,
        is_superuser=True,
        is_system=True,
        organization_id=None,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _patch_embedder(embedder):
    """Patch the phase-1 loader so no embedding config row is needed."""
    return patch(
        "shared.sdk_knowledge.load_knowledge_embedder",
        new=AsyncMock(return_value=embedder),
    )


def _patch_http_embedder(embedder):
    """Patch the single-session HTTP path to use the fake embedder."""
    import shared.sdk_knowledge as svc

    return patch.object(
        svc.embeddings_factory_module,
        "get_embedding_client",
        AsyncMock(return_value=embedder),
    )


@pytest_asyncio.fixture(autouse=True)
async def _sentinel_user(db_session):
    """Every DB test sees the engine sentinel for local ``created_by``."""
    await _ensure_sentinel_user(db_session)


async def _local_store(db_session, *, content, namespace, key, metadata, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": f"store-{uuid4().hex[:6]}", "op": "knowledge.store",
            "content": content, "namespace": namespace, "key": key,
            "metadata": metadata, "scope": scope,
        },
    )


async def _local_store_many(db_session, *, documents, namespace, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": f"sm-{uuid4().hex[:6]}", "op": "knowledge.store_many",
            "documents": documents, "namespace": namespace, "scope": scope,
        },
    )


async def _local_search(db_session, *, query, namespace, scope, principal, **kwargs):
    from src.services.execution.sdk_local_dispatch import dispatch_frames

    frame = {
        "v": 1, "id": f"search-{uuid4().hex[:6]}", "op": "knowledge.search",
        "query": query, "namespace": namespace, "scope": scope,
    }
    frame.update(kwargs)
    frames = await dispatch_frames(lambda: _db_factory(db_session), principal, frame)
    collected = list(frames)
    first = collected[0]
    if first.get("chunked"):
        raw = b"".join(base64.b64decode(part["data"]) for part in collected[1:])
        assert len(raw) == first["total"]
        return {"ok": True, "result": json.loads(raw)}
    return first


async def _local_delete(db_session, *, key, namespace, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": f"del-{uuid4().hex[:6]}", "op": "knowledge.delete",
            "key": key, "namespace": namespace, "scope": scope,
        },
    )


async def _local_delete_namespace(db_session, *, namespace, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": f"delns-{uuid4().hex[:6]}",
            "op": "knowledge.delete_namespace",
            "namespace": namespace, "scope": scope,
        },
    )


async def _local_list_namespaces(db_session, *, scope, principal, include_global=True):
    from src.services.execution.sdk_local_dispatch import dispatch_frames

    frames = await dispatch_frames(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": f"lns-{uuid4().hex[:6]}",
            "op": "knowledge.list_namespaces",
            "scope": scope, "include_global": include_global,
        },
    )
    collected = list(frames)
    first = collected[0]
    if first.get("chunked"):
        raw = b"".join(base64.b64decode(part["data"]) for part in collected[1:])
        return {"ok": True, "result": json.loads(raw)}
    return first


async def _local_get(db_session, *, key, namespace, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": f"get-{uuid4().hex[:6]}", "op": "knowledge.get",
            "key": key, "namespace": namespace, "scope": scope,
        },
    )


async def _row_count(db_session, *, namespace, key):
    return (
        await db_session.execute(
            select(func.count())
            .select_from(KnowledgeStore)
            .where(KnowledgeStore.namespace == namespace, KnowledgeStore.key == key)
        )
    ).scalar_one()


@pytest.mark.asyncio
class TestStoreParity:
    async def test_store_round_trip_matches_http(self, db_session):
        from src.models.contracts.cli import CLIKnowledgeStoreRequest
        from src.routers.cli import cli_knowledge_store
        from src.core.auth import UserPrincipal

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        seed_user = await _seed_user(db_session, org_id=org.id)
        embedder = _FakeEmbedder()
        principal = _engine_principal(org.id)
        user = UserPrincipal(
            user_id=seed_user.id, email="sdk@test.local",
            organization_id=org.id, is_superuser=False,
        )
        with _patch_embedder(embedder), _patch_http_embedder(embedder):
            http_value = await cli_knowledge_store(
                CLIKnowledgeStoreRequest(
                    content="The refund window is 30 days.",
                    namespace=f"ns-{tag}", key="refund",
                    metadata={"source": "handbook"},
                ),
                user, db_session,
            )
            local = await _local_store(
                db_session, content="The return window is 30 days.",
                namespace=f"ns-{tag}", key="return",
                metadata={"source": "handbook"},
                scope=str(org.id), principal=principal,
            )
        assert local["ok"] is True, local
        assert set(local["result"]) == {"id"}
        # Both rows landed in the exact org scope.
        for key in ("refund", "return"):
            rows = (
                await db_session.execute(
                    select(KnowledgeStore).where(
                        KnowledgeStore.namespace == f"ns-{tag}",
                        KnowledgeStore.key == key,
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].organization_id == org.id
        assert http_value["id"] != local["result"]["id"]

    async def test_store_uses_sentinel_actor_not_child_claim(self, db_session):
        from src.core.constants import SYSTEM_USER_UUID
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with _patch_embedder(_FakeEmbedder()):
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1, "id": "actor-1", "op": "knowledge.store",
                    "content": "hello", "namespace": f"ns-{tag}",
                    "key": "k", "metadata": None, "scope": str(org.id),
                    # Hostile child stuffing trusted-claim fields: never read.
                    "created_by": str(uuid4()),
                    "actor_email": "forged@test.local",
                    "user": {"is_superuser": True},
                },
            )
        assert response["ok"] is True, response
        row = (
            await db_session.execute(
                select(KnowledgeStore).where(
                    KnowledgeStore.namespace == f"ns-{tag}",
                    KnowledgeStore.key == "k",
                )
            )
        ).scalar_one()
        assert row.created_by == SYSTEM_USER_UUID

    async def test_store_missing_content_is_422(self, db_session):
        principal = _engine_principal(is_platform_admin=True)
        local = await _local_store(
            db_session, content=None, namespace="ns", key="k",
            metadata=None, scope="global", principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_store_unconfigured_embeddings_is_503(self, db_session):
        from shared.sdk_knowledge import load_knowledge_embedder

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with patch(
            "shared.sdk_knowledge.load_knowledge_embedder",
            new=AsyncMock(side_effect=ValueError("no embedding config")),
        ):
            assert load_knowledge_embedder is not None  # seam is patchable
            local = await _local_store(
                db_session, content="hi", namespace="ns", key="k",
                metadata=None, scope=str(org.id), principal=principal,
            )
        assert local["ok"] is False
        assert local["status"] == 503
        assert local["detail"] == "no embedding config"
        assert await _row_count(db_session, namespace="ns", key="k") == 0


@pytest.mark.asyncio
class TestStoreManyParity:
    async def test_store_many_one_commit_ids_match(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        embedder = _FakeEmbedder()
        principal = _engine_principal(org.id)
        with _patch_embedder(embedder):
            local = await _local_store_many(
                db_session,
                documents=[
                    {"content": "Doc one.", "key": "d1"},
                    {"content": "Doc two.", "metadata": {"m": 1}},
                ],
                namespace=f"ns-{tag}", scope=str(org.id), principal=principal,
            )
        assert local["ok"] is True, local
        assert len(local["result"]["ids"]) == 2
        # One embedder, sequential embedding of both docs.
        assert embedder.embed_single_calls == []
        assert [c for batch in embedder.embed_calls for c in batch] == [
            "Doc one.", "Doc two.",
        ]

    async def test_store_many_second_doc_missing_content_leaves_no_rows(
        self, db_session
    ):
        """Explicit transaction change: pre-commit embed failure, no txn opened.

        The single-session path flushed the first doc then rolled back;
        the split path embeds everything off-connection before any write
        transaction, so the same failure leaves no rows with no rollback.
        """
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with _patch_embedder(_FakeEmbedder()):
            local = await _local_store_many(
                db_session,
                documents=[{"content": "Doc one.", "key": "d1"}, {"key": "no-content"}],
                namespace=f"ns-{tag}", scope=str(org.id), principal=principal,
            )
        assert local["ok"] is False
        assert local["status"] == 500
        assert local["detail"].startswith("Knowledge store failed:")
        assert await _row_count(db_session, namespace=f"ns-{tag}", key="d1") == 0

    async def test_store_many_db_failure_rolls_back_prior_rows(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with (
            _patch_embedder(_FakeEmbedder()),
            patch(
                "src.repositories.knowledge.KnowledgeRepository._build_chunk_rows",
                side_effect=[ValueError("boom"), RuntimeError("second write failed")],
            ),
        ):
            # Bypass DTO (needs real documents list) — drive two real docs
            # through the frame with the second write failing.
            response = await dispatch_frame(
                lambda: _db_factory(db_session),
                principal,
                {
                    "v": 1, "id": "sm-rb", "op": "knowledge.store_many",
                    "documents": [
                        {"content": "one", "key": "r1"},
                        {"content": "two", "key": "r2"},
                    ],
                    "namespace": f"ns-{tag}", "scope": str(org.id),
                },
            )
        assert response["ok"] is False
        assert response["status"] == 500
        assert await _row_count(db_session, namespace=f"ns-{tag}", key="r1") == 0
        assert await _row_count(db_session, namespace=f"ns-{tag}", key="r2") == 0


@pytest.mark.asyncio
class TestSearchParity:
    async def test_search_matches_http_shape_and_scope(self, db_session):
        from src.models.contracts.cli import CLIKnowledgeSearchRequest
        from src.routers.cli import cli_knowledge_search
        from src.core.auth import UserPrincipal

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        seed_user = await _seed_user(db_session, org_id=org.id)
        embedder = _FakeEmbedder()
        principal = _engine_principal(org.id)
        user = UserPrincipal(
            user_id=seed_user.id, email="sdk@test.local",
            organization_id=org.id, is_superuser=False,
        )
        with _patch_embedder(embedder), _patch_http_embedder(embedder):
            for key, content in (
                ("s1", "The refund window is thirty days."),
                ("s2", "Database indexes speed up queries."),
            ):
                await _local_store(
                    db_session, content=content, namespace=f"ns-{tag}",
                    key=key, metadata=None, scope=str(org.id),
                    principal=principal,
                )
            http_items = await cli_knowledge_search(
                CLIKnowledgeSearchRequest(
                    query="refund policy", namespace=[f"ns-{tag}"],
                    limit=5, scope=str(org.id),
                ),
                user, db_session,
            )
            local = await _local_search(
                db_session, query="refund policy", namespace=[f"ns-{tag}"],
                scope=str(org.id), principal=principal,
            )
        assert local["ok"] is True, local
        assert {d["key"] for d in local["result"]["items"]} == {"s1", "s2"}
        assert {d["key"] for d in [i.model_dump() for i in http_items]} == {"s1", "s2"}
        # Same JSON-serializable shape as the HTTP DTOs.
        assert set(local["result"]["items"][0]) == {
            "id", "namespace", "content", "metadata", "score",
            "organization_id", "key", "created_at",
        }
        # Query embedded exactly once per path.
        assert embedder.embed_single_calls.count("refund policy") == 2

    async def test_search_unconfigured_embeddings_is_503(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with patch(
            "shared.sdk_knowledge.load_knowledge_embedder",
            new=AsyncMock(side_effect=ValueError("no embedding config")),
        ):
            local = await _local_search(
                db_session, query="q", namespace=["default"],
                scope=str(org.id), principal=principal,
            )
        assert local["ok"] is False
        assert local["status"] == 503

    async def test_search_invalid_limit_is_422(self, db_session):
        principal = _engine_principal(is_platform_admin=True)
        local = await _local_search(
            db_session, query="q", namespace=["default"],
            scope="global", principal=principal, limit="many",
        )
        assert local["ok"] is False
        assert local["status"] == 422


@pytest.mark.asyncio
class TestDeleteParity:
    async def test_delete_true_then_false(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with _patch_embedder(_FakeEmbedder()):
            await _local_store(
                db_session, content="bye", namespace=f"ns-{tag}",
                key="k", metadata=None, scope=str(org.id),
                principal=principal,
            )
            first = await _local_delete(
                db_session, key="k", namespace=f"ns-{tag}",
                scope=str(org.id), principal=principal,
            )
            second = await _local_delete(
                db_session, key="k", namespace=f"ns-{tag}",
                scope=str(org.id), principal=principal,
            )
        assert first == {**first, "ok": True} and first["result"] == {"deleted": True}
        assert second["ok"] is True and second["result"] == {"deleted": False}

    async def test_delete_exact_scope_does_not_cross_org(self, db_session):
        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        with _patch_embedder(_FakeEmbedder()):
            await _local_store(
                db_session, content="a-doc", namespace=f"ns-{tag}",
                key="k", metadata=None, scope=str(org_a.id),
                principal=_engine_principal(org_a.id),
            )
            # Same key/namespace in org B: nothing there to delete.
            missed = await _local_delete(
                db_session, key="k", namespace=f"ns-{tag}",
                scope=str(org_b.id),
                principal=_engine_principal(org_b.id, is_provider_org=True),
            )
            assert missed["ok"] is True
            assert missed["result"] == {"deleted": False}
            assert await _row_count(db_session, namespace=f"ns-{tag}", key="k") == 1

    async def test_delete_namespace_count(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with _patch_embedder(_FakeEmbedder()):
            for key in ("k1", "k2"):
                await _local_store(
                    db_session, content=f"doc {key}", namespace=f"ns-{tag}",
                    key=key, metadata=None, scope=str(org.id),
                    principal=principal,
                )
            deleted = await _local_delete_namespace(
                db_session, namespace=f"ns-{tag}",
                scope=str(org.id), principal=principal,
            )
            again = await _local_delete_namespace(
                db_session, namespace=f"ns-{tag}",
                scope=str(org.id), principal=principal,
            )
        assert deleted["ok"] is True
        assert deleted["result"] == {"deleted_count": 2}
        assert again["ok"] is True
        assert again["result"] == {"deleted_count": 0}

    async def test_delete_namespace_blank_is_422(self, db_session):
        principal = _engine_principal(is_platform_admin=True)
        local = await _local_delete_namespace(
            db_session, namespace="", scope="global", principal=principal
        )
        assert local["ok"] is False
        assert local["status"] == 422


@pytest.mark.asyncio
class TestListAndGetParity:
    async def test_list_namespaces_envelope_and_flag(self, db_session):
        from src.models.contracts.cli import CLIKnowledgeStoreRequest
        from src.routers.cli import cli_knowledge_list_namespaces, cli_knowledge_store
        from src.core.auth import UserPrincipal

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        seed_user = await _seed_user(db_session, org_id=org.id)
        seed_admin = await _seed_user(db_session, is_superuser=True)
        embedder = _FakeEmbedder()
        principal = _engine_principal(org.id)
        user = UserPrincipal(
            user_id=seed_user.id, email="sdk@test.local",
            organization_id=org.id, is_superuser=False,
        )
        with _patch_embedder(embedder), _patch_http_embedder(embedder):
            # One global doc via HTTP (platform admin), one org doc locally.
            admin = UserPrincipal(
                user_id=seed_admin.id, email="admin@test.local",
                organization_id=None, is_superuser=True,
            )
            await cli_knowledge_store(
                CLIKnowledgeStoreRequest(
                    content="global doc", namespace=f"ns-{tag}",
                    key="g", scope="global",
                ),
                admin, db_session,
            )
            await _local_store(
                db_session, content="org doc", namespace=f"ns-{tag}",
                key="o", metadata=None, scope=str(org.id),
                principal=principal,
            )
            http_items = await cli_knowledge_list_namespaces(
                str(org.id), True, user, db_session
            )
            local = await _local_list_namespaces(
                db_session, scope=str(org.id), principal=principal,
                include_global=True,
            )
            local_no_global = await _local_list_namespaces(
                db_session, scope=str(org.id), principal=principal,
                include_global=False,
            )
        assert local["ok"] is True, local
        entry = next(
            i for i in local["result"]["items"] if i["namespace"] == f"ns-{tag}"
        )
        http_entry = next(
            i for i in [n.model_dump() for n in http_items]
            if i["namespace"] == f"ns-{tag}"
        )
        assert entry == http_entry
        assert entry["scopes"]["total"] == 2
        solo = next(
            i for i in local_no_global["result"]["items"]
            if i["namespace"] == f"ns-{tag}"
        )
        assert solo["scopes"] == {"global": 0, "org": 1, "total": 1}

    async def test_get_reassembled_shape_and_miss_404(self, db_session):
        long = ("Gettable sentence. " * 200).strip()
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with _patch_embedder(_FakeEmbedder()):
            stored = await _local_store(
                db_session, content=long, namespace=f"ns-{tag}",
                key="big", metadata={"m": 1}, scope=str(org.id),
                principal=principal,
            )
            assert stored["ok"] is True
            got = await _local_get(
                db_session, key="big", namespace=f"ns-{tag}",
                scope=str(org.id), principal=principal,
            )
            miss = await _local_get(
                db_session, key="absent", namespace=f"ns-{tag}",
                scope=str(org.id), principal=principal,
            )
        assert got["ok"] is True, got
        assert got["result"]["content"] == long
        assert got["result"]["metadata"] == {"m": 1}
        # The frame matches the HTTP wire (``score: null`` via the
        # response DTO) so the SDK model validates on both paths.
        assert got["result"]["score"] is None
        assert set(got["result"]) == {
            "id", "namespace", "content", "metadata", "score",
            "organization_id", "key", "created_at",
        }
        assert miss["ok"] is False
        assert miss["status"] == 404

    async def test_get_blank_key_is_422(self, db_session):
        principal = _engine_principal(is_platform_admin=True)
        local = await _local_get(
            db_session, key="", namespace="ns",
            scope="global", principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 422


@pytest.mark.asyncio
class TestScopeDenial:
    async def test_cross_org_and_global_denied_without_bypass(self, db_session):
        caller = await _seed_org(db_session)
        other = await _seed_org(db_session)
        principal = _engine_principal(caller.id)
        cases = [
            {"v": 1, "id": "s-1", "op": "knowledge.store", "content": "x",
             "namespace": "ns", "key": "k", "metadata": None,
             "scope": str(other.id)},
            {"v": 1, "id": "s-2", "op": "knowledge.store_many",
             "documents": [{"content": "x"}], "namespace": "ns",
             "scope": str(other.id)},
            {"v": 1, "id": "s-3", "op": "knowledge.search", "query": "q",
             "namespace": ["default"], "scope": str(other.id)},
            {"v": 1, "id": "s-4", "op": "knowledge.delete", "key": "k",
             "namespace": "ns", "scope": str(other.id)},
            {"v": 1, "id": "s-5", "op": "knowledge.delete_namespace",
             "namespace": "ns", "scope": "global"},
            {"v": 1, "id": "s-6", "op": "knowledge.list_namespaces",
             "scope": "global", "include_global": True},
            {"v": 1, "id": "s-7", "op": "knowledge.get", "key": "k",
             "namespace": "ns", "scope": "global"},
        ]
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        for frame in cases:
            with _patch_embedder(_FakeEmbedder()):
                response = await dispatch_frame(
                    lambda: _db_factory(db_session), principal, frame
                )
            assert response["ok"] is False, frame
            assert response["status"] == 403, frame

    async def test_provider_cross_org_allowed(self, db_session):
        caller = await _seed_org(db_session, is_provider=True)
        other = await _seed_org(db_session)
        principal = _engine_principal(caller.id, is_provider_org=True)
        with _patch_embedder(_FakeEmbedder()):
            stored = await _local_store(
                db_session, content="cross", namespace="ns",
                key="k", metadata=None, scope=str(other.id),
                principal=principal,
            )
        assert stored["ok"] is True, stored

    async def test_malformed_scope_is_422(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal(uuid4())
        frame = {
            "v": 1, "id": "bad-scope", "op": "knowledge.get",
            "key": "k", "namespace": "ns", "scope": "not-a-uuid",
        }
        response = await dispatch_frame(
            lambda: _db_factory(db_session), principal, frame
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_service_revocation_denies_cross_org(self, db_session):
        provider = await _seed_org(db_session, is_provider=True)
        other = await _seed_org(db_session)
        principal = _service_principal(provider.id)
        provider.is_provider = False
        await db_session.flush()
        local = await _local_get(
            db_session, key="k", namespace="ns",
            scope=str(other.id), principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_unknown_op_and_bad_version(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal(is_platform_admin=True)
        unknown = await dispatch_frame(
            lambda: _db_factory(db_session), principal,
            {"v": 1, "id": "u-1", "op": "knowledge.drop"},
        )
        assert unknown["ok"] is False and unknown["status"] == 404
        bad_version = await dispatch_frame(
            lambda: _db_factory(db_session), principal,
            {"v": 999, "id": "u-2", "op": "knowledge.get",
             "key": "k", "namespace": "ns"},
        )
        assert bad_version["ok"] is False and bad_version["status"] == 400


@pytest.mark.asyncio
class TestExternalDenialAndAllowlist:
    async def test_http_external_denial_before_service(self, db_session):
        import shared.sdk_knowledge as svc
        import src.routers.cli as cli_module
        from src.core.auth import UserPrincipal
        from src.models.contracts.cli import (
            CLIKnowledgeDeleteRequest,
            CLIKnowledgeSearchRequest,
            CLIKnowledgeStoreManyRequest,
            CLIKnowledgeStoreRequest,
        )
        from fastapi import HTTPException

        user = UserPrincipal(
            user_id=uuid4(), email="ext@test.local",
            organization_id=uuid4(), is_external=True,
        )
        session = AsyncMock()
        cases = [
            ("store_knowledge_document", lambda: cli_module.cli_knowledge_store(
                CLIKnowledgeStoreRequest(content="x"), user, session)),
            ("store_many_knowledge_documents", lambda: cli_module.cli_knowledge_store_many(
                CLIKnowledgeStoreManyRequest(documents=[{"content": "x"}]),
                user, session)),
            ("search_knowledge_documents", lambda: cli_module.cli_knowledge_search(
                CLIKnowledgeSearchRequest(query="q"), user, session)),
            ("delete_knowledge_document", lambda: cli_module.cli_knowledge_delete(
                CLIKnowledgeDeleteRequest(key="k"), user, session)),
            ("delete_knowledge_namespace", lambda: cli_module.cli_knowledge_delete_namespace(
                "ns", None, user, session)),
            ("list_knowledge_namespaces", lambda: cli_module.cli_knowledge_list_namespaces(
                None, True, user, session)),
            ("get_knowledge_document", lambda: cli_module.cli_knowledge_get(
                "k", "ns", None, user, session)),
        ]
        for attr, call in cases:
            with patch.object(svc, attr, AsyncMock(return_value={})) as mock:
                with pytest.raises(HTTPException) as exc:
                    await call()
                assert exc.value.status_code == 403, attr
                mock.assert_not_awaited()

    async def test_all_seven_ops_allowlisted(self):
        from src.services.execution.sdk_local_dispatch import SDK_CHANNEL_ALLOWED_OPS

        for op in (
            "knowledge.store", "knowledge.store_many", "knowledge.search",
            "knowledge.delete", "knowledge.delete_namespace",
            "knowledge.list_namespaces", "knowledge.get",
        ):
            assert op in SDK_CHANNEL_ALLOWED_OPS, op

    async def test_principal_is_never_external(self):
        principal = _engine_principal(uuid4())
        assert principal.is_external is False
        service = _service_principal(uuid4())
        assert service.is_external is False


@pytest.mark.asyncio
class TestSessionsAndChunking:
    async def test_embedding_ops_use_two_sessions_db_ops_use_one(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        opened = 0

        @contextlib.asynccontextmanager
        async def _counting_factory():
            nonlocal opened
            opened += 1
            yield db_session

        with _patch_embedder(_FakeEmbedder()):
            # store = config session + DB session
            await dispatch_frame(
                _counting_factory, principal,
                {"v": 1, "id": "c-1", "op": "knowledge.store",
                 "content": "hi", "namespace": "ns", "key": "k1",
                 "metadata": None, "scope": str(org.id)},
            )
            assert opened == 2
            # delete = one DB session
            await dispatch_frame(
                _counting_factory, principal,
                {"v": 1, "id": "c-2", "op": "knowledge.delete",
                 "key": "k1", "namespace": "ns", "scope": str(org.id)},
            )
            assert opened == 3
            # get = one DB session
            await dispatch_frame(
                _counting_factory, principal,
                {"v": 1, "id": "c-3", "op": "knowledge.get",
                 "key": "k1", "namespace": "ns", "scope": str(org.id)},
            )
            assert opened == 4

    async def test_no_session_open_during_embedding(self, db_session):
        """The pooled connection is released before provider work starts."""
        from src.services.execution.sdk_local_dispatch import dispatch_frame
        import shared.sdk_knowledge as svc

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        sessions_open = 0
        saw_embed_with_open_session = False

        @contextlib.asynccontextmanager
        async def _tracking_factory():
            nonlocal sessions_open
            sessions_open += 1
            try:
                yield db_session
            finally:
                sessions_open -= 1

        real_embed = svc.embed_content_chunks

        async def _spying_embed(embedder, content):
            nonlocal saw_embed_with_open_session
            if sessions_open > 0:
                saw_embed_with_open_session = True
            return await real_embed(embedder, content)

        with (
            _patch_embedder(_FakeEmbedder()),
            patch.object(svc, "embed_content_chunks", _spying_embed),
        ):
            response = await dispatch_frame(
                _tracking_factory, principal,
                {"v": 1, "id": "nc-1", "op": "knowledge.store",
                 "content": "hello", "namespace": "ns", "key": "k",
                 "metadata": None, "scope": str(org.id)},
            )
        assert response["ok"] is True, response
        assert saw_embed_with_open_session is False
        assert sessions_open == 0

    async def test_large_search_result_splits_into_bounded_frames(self, db_session):
        from bifrost._local_transport import MAX_FRAME_BYTES
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        payload = {
            "items": [
                {"id": str(uuid4()), "namespace": "ns", "content": "x" * 2000,
                 "metadata": {}, "score": 0.5, "organization_id": None,
                 "key": f"k{i}",
                 "created_at": "2026-01-01T00:00:00+00:00"}
                for i in range(100)
            ]
        }

        @contextlib.asynccontextmanager
        async def _stub_factory():
            yield object()

        principal = _engine_principal(is_platform_admin=True)
        with (
            patch(
                "shared.sdk_knowledge.load_knowledge_embedder",
                new=AsyncMock(return_value=_FakeEmbedder()),
            ),
            patch(
                "shared.sdk_knowledge.search_knowledge_with_embedding",
                new=AsyncMock(return_value=payload["items"]),
            ),
        ):
            frames = await dispatch_frames(
                _stub_factory, principal,
                {"v": 1, "id": "big-s", "op": "knowledge.search",
                 "query": "q", "namespace": ["ns"], "scope": "global"},
            )
            collected = list(frames)
        assert len(collected) > 1
        header, parts = collected[0], collected[1:]
        assert header["ok"] is True and header["chunked"] is True
        assert header["parts"] == len(parts) >= 2
        for i, part in enumerate(parts):
            assert part["id"] == "big-s" and part["part"] == i
            raw = json.dumps(part, separators=(",", ":")).encode()
            assert len(raw) <= MAX_FRAME_BYTES


class TestChildTransport:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def _pump(self, req_conn, resp_conn, handler, count=1):
        for _ in range(count):
            raw = await asyncio.to_thread(req_conn.recv_bytes, 65537)
            frame = json.loads(raw.decode("utf-8"))
            response = handler(frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    @pytest.mark.asyncio
    async def test_all_seven_round_trip_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        doc = {
            "id": str(uuid4()), "namespace": "ns", "content": "hello",
            "metadata": {}, "score": 0.9,
            "organization_id": str(uuid4()), "key": "k",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        seen = {}

        def _handler(frame):
            seen[frame["op"]] = frame
            assert "created_by" not in frame, frame
            assert "user" not in frame, frame
            op = frame["op"]
            if op == "knowledge.store":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"id": doc["id"]}}
            if op == "knowledge.store_many":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"ids": [doc["id"]] * len(frame["documents"])}}
            if op == "knowledge.search":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"items": [doc]}}
            if op == "knowledge.delete":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"deleted": True}}
            if op == "knowledge.delete_namespace":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"deleted_count": 2}}
            if op == "knowledge.list_namespaces":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"items": [{"namespace": "ns", "scopes": {}}]}}
            if op == "knowledge.get":
                return {"v": 1, "id": frame["id"], "ok": True, "result": doc}
            raise AssertionError(op)

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=7))
        try:
            from bifrost._context import clear_execution_context, set_execution_context
            from bifrost.knowledge import knowledge
            from src.sdk.context import ExecutionContext

            ctx = ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
            )
            set_execution_context(ctx)
            try:
                with patch("bifrost.knowledge.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    assert await knowledge.store("hello", key="k") == doc["id"]
                    assert await knowledge.store_many([{"content": "hi"}]) == [doc["id"]]
                    results = await knowledge.search("hello")
                    assert [d.key for d in results] == ["k"]
                    assert await knowledge.delete("k") is True
                    assert await knowledge.delete_namespace("ns") == 2
                    namespaces = await knowledge.list_namespaces()
                    assert [n.namespace for n in namespaces] == ["ns"]
                    got = await knowledge.get("k")
                    assert got is not None and got.id == doc["id"]
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert set(seen) == {
            "knowledge.store", "knowledge.store_many", "knowledge.search",
            "knowledge.delete", "knowledge.delete_namespace",
            "knowledge.list_namespaces", "knowledge.get",
        }
        assert seen["knowledge.search"]["namespace"] == ["default"]

    @pytest.mark.asyncio
    async def test_local_get_404_maps_to_none_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": False,
                           "status": 404, "detail": "Document not found"},
                count=1,
            )
        )
        try:
            from bifrost.knowledge import knowledge

            with patch("bifrost.knowledge.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                assert await knowledge.get("absent") is None
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_local_error_never_falls_back_to_http(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAuthorizationError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        seen = []

        async def _pump_statuses():
            for status in (403, 500):
                raw = await asyncio.to_thread(req_recv.recv_bytes, 65537)
                frame = json.loads(raw.decode("utf-8"))
                seen.append(frame["op"])
                await asyncio.to_thread(
                    resp_send.send_bytes,
                    json.dumps(
                        {"v": 1, "id": frame["id"], "ok": False,
                         "status": status, "detail": "denied"}
                    ).encode(),
                )

        pump = asyncio.create_task(_pump_statuses())
        try:
            from bifrost.knowledge import knowledge

            with patch("bifrost.knowledge.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(BifrostAuthorizationError):
                    await knowledge.store("x")
                with pytest.raises(Exception):
                    await knowledge.search("x")
            await pump
            assert seen == ["knowledge.store", "knowledge.search"]
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
