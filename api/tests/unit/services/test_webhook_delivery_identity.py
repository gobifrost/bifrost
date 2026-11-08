"""Generic identity admission and retained-Event semantics."""

from uuid import uuid4

import pytest

from shared.webhook_delivery_identity import accept_identified_webhook
from src.models.enums import EventSourceType
from src.models.orm.events import EventSource
from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.protocol import Deliver, Rejected, WebhookRequest
from src.services.webhooks.signing_secret import with_signing_secret
from tests.unit.services.test_webhook_adapters import _sign


@pytest.mark.parametrize("identity", ["", "a" * 129, "with space", "with\nnewline", "nonascii-\u00e9"])
async def test_missing_or_invalid_provider_identity_is_refused(identity):
    body = b'{}'
    request = WebhookRequest("POST", "/hook", {
        "x-delivery-id": identity, "x-signature-256": _sign(body, "secret"),
    }, {}, body)
    result = await GenericWebhookAdapter().handle_request(request,
        {"delivery_id_header": "X-Delivery-ID"}, with_signing_secret({}, "secret"))
    assert isinstance(result, Rejected) and result.status_code == 400


async def test_delivery_identity_requires_signature_verification():
    request = WebhookRequest("POST", "/hook", {"x-delivery-id": "123"}, {}, b'{}')
    result = await GenericWebhookAdapter().handle_request(request,
        {"delivery_id_header": "X-Delivery-ID"}, {})
    assert isinstance(result, Rejected) and result.status_code == 503


@pytest.mark.parametrize("header", [True, False, 0, "bad header", "a" * 129])
async def test_invalid_delivery_header_configuration_fails_closed(header):
    body = b'{}'
    request = WebhookRequest("POST", "/hook", {"x-signature-256": _sign(body, "secret")}, {}, body)
    result = await GenericWebhookAdapter().handle_request(request,
        {"delivery_id_header": header}, with_signing_secret({}, "secret"))
    assert isinstance(result, Rejected) and result.status_code == 503


async def test_opt_in_identity_is_taken_only_after_signature_verification():
    body = b'{}'
    request = WebhookRequest("POST", "/hook", {
        "x-delivery-id": "3852823007498076160", "x-signature-256": _sign(body, "secret"),
    }, {}, body)
    adapter = GenericWebhookAdapter()
    config = {"delivery_id_header": "X-Delivery-ID"}
    state = with_signing_secret({}, "secret")
    accepted = await adapter.handle_request(request, config, state)
    assert isinstance(accepted, Deliver)
    assert accepted.delivery_id == "3852823007498076160"
    request.headers["x-signature-256"] = "sha256=" + "0" * 64
    rejected = await adapter.handle_request(request, config, state)
    assert isinstance(rejected, Rejected) and rejected.status_code == 401


async def test_identity_is_source_scoped_and_ends_when_event_history_is_deleted(db_session):
    sources = [EventSource(name=f"Identity {uuid4()}", source_type=EventSourceType.WEBHOOK,
                          created_by="test") for _ in range(2)]
    db_session.add_all(sources)
    await db_session.flush()
    request = WebhookRequest("POST", "/hook", {}, {}, b'{}')
    incoming = Deliver(data={}, event_type="changed", delivery_id="same-provider-id")
    first = await accept_identified_webhook(db_session, sources[0], incoming, request)
    duplicate = await accept_identified_webhook(db_session, sources[0], incoming, request)
    other_source = await accept_identified_webhook(db_session, sources[1], incoming, request)
    assert not isinstance(first, Rejected) and not isinstance(duplicate, Rejected)
    assert not isinstance(other_source, Rejected)
    assert first[1] and not duplicate[1] and other_source[1]
    assert first[0].id == duplicate[0].id != other_source[0].id
    old_id = first[0].id
    await db_session.delete(first[0])
    await db_session.flush()
    recreated = await accept_identified_webhook(db_session, sources[0], incoming, request)
    assert not isinstance(recreated, Rejected)
    assert recreated[1] and recreated[0].id != old_id


@pytest.mark.parametrize("body,event_type", [(b'{"changed":true}', "changed"), (b'{}', "different")])
async def test_reusing_an_identity_with_different_content_is_refused(db_session, body, event_type):
    source = EventSource(name=f"Identity {uuid4()}", source_type=EventSourceType.WEBHOOK, created_by="test")
    db_session.add(source)
    await db_session.flush()
    original = Deliver(data={}, event_type="changed", delivery_id="one")
    await accept_identified_webhook(db_session, source, original, WebhookRequest("POST", "/", {}, {}, b'{}'))
    changed = Deliver(data={}, event_type=event_type, delivery_id="one")
    result = await accept_identified_webhook(db_session, source, changed, WebhookRequest("POST", "/", {}, {}, body))
    assert isinstance(result, Rejected) and result.status_code == 409
