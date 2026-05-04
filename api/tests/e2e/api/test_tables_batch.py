"""
E2E tests for batch table-document operations against REST endpoints.

Document ``id`` is a globally-unique primary key, so all IDs must be
unique across all tables. Tests use UUID-suffixed IDs.

Auto-create-on-insert lives in the SDK (``bifrost.tables`` runs a
404 → POST /api/tables → retry on the first write); the REST handlers
themselves return 404 when the table is missing. This file exercises
the REST handlers directly, so each test creates its table first.
"""

import logging
from uuid import uuid4

logger = logging.getLogger(__name__)


def _uid(prefix: str = "") -> str:
    """Generate a globally unique ID with optional prefix."""
    return f"{prefix}{uuid4().hex[:12]}"


def _create_table(e2e_client, headers, name: str) -> str:
    resp = e2e_client.post(
        "/api/tables",
        headers=headers,
        json={"name": name},
    )
    assert resp.status_code == 201, f"Create table failed: {resp.text}"
    return resp.json()["id"]


class TestInsertBatch:
    """Batch insert via POST /api/tables/{id}/documents/batch."""

    def test_insert_batch(self, e2e_client, platform_admin):
        """Insert multiple documents in a single batch."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_batch_{uuid4().hex[:8]}"
        )
        response = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "documents": [
                    {"data": {"name": "Acme Corp", "status": "active"}},
                    {"data": {"name": "Beta Inc", "status": "pending"}},
                    {"data": {"name": "Gamma LLC", "status": "active"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["inserted"] == 3
        assert len(data["documents"]) == 3
        for doc in data["documents"]:
            assert doc["id"] is not None
            assert doc["table_id"] is not None
            assert "name" in doc["data"]

    def test_insert_batch_with_custom_ids(self, e2e_client, platform_admin):
        """Insert batch with caller-provided IDs."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_batch_{uuid4().hex[:8]}"
        )
        id1, id2 = _uid("acme-"), _uid("beta-")
        response = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "documents": [
                    {"id": id1, "data": {"name": "Acme Corp"}},
                    {"id": id2, "data": {"name": "Beta Inc"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["inserted"] == 2
        ids = {doc["id"] for doc in data["documents"]}
        assert ids == {id1, id2}

class TestUpsertBatch:
    """Batch upsert via POST /api/tables/{id}/documents/batch with upsert=true."""

    def test_upsert_batch_creates_new(self, e2e_client, platform_admin):
        """Upsert batch creates all new documents."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_upsert_{uuid4().hex[:8]}"
        )
        id1, id2 = _uid("emp-"), _uid("emp-")
        response = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "upsert": True,
                "documents": [
                    {"id": id1, "data": {"name": "John", "dept": "Eng"}},
                    {"id": id2, "data": {"name": "Jane", "dept": "Sales"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["inserted"] == 2
        ids = {doc["id"] for doc in data["documents"]}
        assert ids == {id1, id2}

    def test_upsert_batch_updates_existing(self, e2e_client, platform_admin):
        """Upsert batch updates existing documents (merge semantics)."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_upsert_{uuid4().hex[:8]}"
        )
        id1, id2 = _uid("emp-"), _uid("emp-")

        first = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "upsert": True,
                "documents": [
                    {"id": id1, "data": {"name": "John", "dept": "Eng"}},
                    {"id": id2, "data": {"name": "Jane", "dept": "Sales"}},
                ],
            },
        )
        assert first.status_code == 200, first.text

        update = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "upsert": True,
                "documents": [
                    {"id": id1, "data": {"dept": "Management"}},
                    {"id": id2, "data": {"dept": "Marketing"}},
                ],
            },
        )
        assert update.status_code == 200, update.text
        data = update.json()
        assert data["inserted"] == 2
        for doc in data["documents"]:
            if doc["id"] == id1:
                assert doc["data"]["dept"] == "Management"
                assert doc["data"]["name"] == "John"  # merge preserves prior fields
            elif doc["id"] == id2:
                assert doc["data"]["dept"] == "Marketing"
                assert doc["data"]["name"] == "Jane"

    def test_upsert_batch_mixed(self, e2e_client, platform_admin):
        """Upsert batch handles a mix of new and existing documents."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_upsert_{uuid4().hex[:8]}"
        )
        existing_id, new_id = _uid("existing-"), _uid("new-")

        seed = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"id": existing_id, "data": {"name": "Old"}},
        )
        assert seed.status_code == 201, seed.text

        response = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "upsert": True,
                "documents": [
                    {"id": existing_id, "data": {"name": "Updated"}},
                    {"id": new_id, "data": {"name": "Brand New"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["inserted"] == 2

        cnt = e2e_client.get(
            f"/api/tables/{table_id}/documents/count",
            headers=platform_admin.headers,
        )
        assert cnt.json()["count"] == 2


class TestDeleteBatch:
    """Batch delete via POST /api/tables/{id}/documents/batch-delete."""

    def test_delete_batch(self, e2e_client, platform_admin):
        """Delete multiple existing documents in one round trip."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_delete_{uuid4().hex[:8]}"
        )
        id1, id2, id3 = _uid("del-"), _uid("del-"), _uid("del-")

        seed = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch",
            headers=platform_admin.headers,
            json={
                "documents": [
                    {"id": id1, "data": {"name": "A"}},
                    {"id": id2, "data": {"name": "B"}},
                    {"id": id3, "data": {"name": "C"}},
                ],
            },
        )
        assert seed.status_code == 200, seed.text

        response = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch-delete",
            headers=platform_admin.headers,
            json={"ids": [id1, id3]},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["deleted"] == 2
        assert set(data["deleted_ids"]) == {id1, id3}

        cnt = e2e_client.get(
            f"/api/tables/{table_id}/documents/count",
            headers=platform_admin.headers,
        )
        assert cnt.json()["count"] == 1

    def test_delete_batch_skips_nonexistent(self, e2e_client, platform_admin):
        """IDs that don't exist in the table are silently skipped."""
        table_id = _create_table(
            e2e_client, platform_admin.headers, f"test_delete_{uuid4().hex[:8]}"
        )
        real_id = _uid("real-")
        seed = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=platform_admin.headers,
            json={"id": real_id, "data": {"name": "Real"}},
        )
        assert seed.status_code == 201, seed.text

        response = e2e_client.post(
            f"/api/tables/{table_id}/documents/batch-delete",
            headers=platform_admin.headers,
            json={"ids": [real_id, _uid("fake-"), _uid("fake-")]},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["deleted"] == 1
        assert data["deleted_ids"] == [real_id]

    def test_delete_batch_nonexistent_table(self, e2e_client, platform_admin):
        """Delete on a missing table returns 404 (REST surface)."""
        response = e2e_client.post(
            f"/api/tables/nonexistent_{uuid4().hex[:8]}/documents/batch-delete",
            headers=platform_admin.headers,
            json={"ids": [_uid(), _uid()]},
        )
        assert response.status_code == 404
