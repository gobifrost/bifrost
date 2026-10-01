"""A webhook source's signing secret is revealed once and never read back.

Create and rotate return the plaintext in ``raw_secret``. Every other read
(get, list, update) carries only ``webhook.secret_set``, and incoming
requests still verify against the stored secret.
"""

import hashlib
import hmac
import importlib.util
import uuid
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.events import WebhookSource

pytestmark = pytest.mark.e2e

MIGRATION = (
    Path(__file__).parents[3] / "alembic" / "versions" / "20261001_webhook_secret_encrypt.py"
)


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _deliver(e2e_client, source: dict, secret: str | None) -> int:
    body = b'{"event": "signed"}'
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Signature-256"] = _sign(body, secret)
    path = urlparse(source["webhook"]["callback_url"]).path
    return e2e_client.post(path, content=body, headers=headers).status_code


@pytest.fixture
def make_source(e2e_client, platform_admin):
    created: list[str] = []

    def make(config: dict) -> dict:
        response = e2e_client.post(
            "/api/events/sources",
            headers=platform_admin.headers,
            json={
                "name": f"E2E Signed Webhook {uuid.uuid4().hex[:8]}",
                "source_type": "webhook",
                "organization_id": None,
                "webhook": {"adapter_name": "generic", "config": config},
            },
        )
        assert response.status_code == 201, response.text
        created.append(response.json()["id"])
        return response.json()

    yield make

    for source_id in created:
        e2e_client.delete(f"/api/events/sources/{source_id}", headers=platform_admin.headers)


def _read_surfaces(e2e_client, headers, source_id: str) -> list[str]:
    get = e2e_client.get(f"/api/events/sources/{source_id}", headers=headers)
    listed = e2e_client.get("/api/events/sources", headers=headers)
    assert get.status_code == 200 and listed.status_code == 200
    return [get.text, listed.text]


def test_create_reveals_the_secret_once_and_reads_never_return_it(
    e2e_client, platform_admin, make_source
):
    secret = f"create-{uuid.uuid4().hex}"

    created = make_source({"secret": secret, "signature_header": "X-Signature-256"})

    assert created["raw_secret"] == secret
    assert created["webhook"]["config"] == {"signature_header": "X-Signature-256"}
    assert created["webhook"]["secret_set"] is True
    for body in _read_surfaces(e2e_client, platform_admin.headers, created["id"]):
        assert secret not in body
        assert '"raw_secret"' not in body
    fetched = e2e_client.get(
        f"/api/events/sources/{created['id']}", headers=platform_admin.headers
    ).json()
    assert fetched["webhook"]["secret_set"] is True
    assert _deliver(e2e_client, created, secret) == 202
    assert _deliver(e2e_client, created, None) == 401


def test_source_without_a_secret_reports_none_set(make_source):
    created = make_source({})

    assert created["raw_secret"] is None
    assert created["webhook"]["secret_set"] is False


def test_rotate_reveals_the_new_secret_once_and_the_old_one_stops_verifying(
    e2e_client, platform_admin, make_source
):
    original = f"original-{uuid.uuid4().hex}"
    source = make_source({"secret": original})

    rotated = e2e_client.post(
        f"/api/events/sources/{source['id']}/rotate-secret",
        headers=platform_admin.headers,
        json={},
    )
    assert rotated.status_code == 200, rotated.text
    generated = rotated.json()["raw_secret"]
    assert generated and generated != original
    assert rotated.json()["webhook"]["secret_set"] is True
    for body in _read_surfaces(e2e_client, platform_admin.headers, source["id"]):
        assert generated not in body
    assert _deliver(e2e_client, source, original) == 401
    assert _deliver(e2e_client, source, generated) == 202

    supplied = f"supplied-{uuid.uuid4().hex}"
    rotated = e2e_client.post(
        f"/api/events/sources/{source['id']}/rotate-secret",
        headers=platform_admin.headers,
        json={"secret": supplied},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["raw_secret"] == supplied
    assert _deliver(e2e_client, source, supplied) == 202


def test_config_update_keeps_the_secret_and_never_returns_it(
    e2e_client, platform_admin, make_source
):
    secret = f"kept-{uuid.uuid4().hex}"
    source = make_source({"secret": secret})

    updated = e2e_client.patch(
        f"/api/events/sources/{source['id']}",
        headers=platform_admin.headers,
        json={"webhook": {"config": {"event_type_field": "event"}}},
    )

    assert updated.status_code == 200, updated.text
    assert secret not in updated.text
    assert "raw_secret" not in updated.json()
    assert updated.json()["webhook"]["secret_set"] is True
    assert _deliver(e2e_client, source, secret) == 202


def test_rotate_requires_an_adapter_that_verifies_a_secret(e2e_client, platform_admin):
    response = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": f"E2E Schedule {uuid.uuid4().hex[:8]}",
            "source_type": "schedule",
            "organization_id": None,
            "schedule": {"cron_expression": "0 9 * * *"},
        },
    )
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    try:
        rotated = e2e_client.post(
            f"/api/events/sources/{source_id}/rotate-secret",
            headers=platform_admin.headers,
            json={},
        )
        assert rotated.status_code == 400, rotated.text
    finally:
        e2e_client.delete(f"/api/events/sources/{source_id}", headers=platform_admin.headers)


@pytest.mark.asyncio
async def test_a_secret_stored_before_the_migration_still_verifies(
    e2e_client, platform_admin, make_source, db_session: AsyncSession
):
    """Rewrite a live source into the pre-migration plaintext shape, run the
    migration's data step on it, and verify a request signed with that secret."""
    legacy = f"legacy-{uuid.uuid4().hex}"
    source = make_source({})
    await db_session.execute(
        update(WebhookSource)
        .where(WebhookSource.event_source_id == UUID(source["id"]))
        .values(config={"secret": legacy}, state={"secret": legacy})
    )
    await db_session.commit()
    spec = importlib.util.spec_from_file_location("webhook_secret_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    await db_session.run_sync(
        lambda session: migration.encrypt_plaintext_webhook_secrets(session.connection())
    )
    await db_session.commit()

    assert _deliver(e2e_client, source, legacy) == 202
    assert _deliver(e2e_client, source, None) == 401
    for body in _read_surfaces(e2e_client, platform_admin.headers, source["id"]):
        assert legacy not in body
