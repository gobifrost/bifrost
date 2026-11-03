"""Wire contracts for the production remote execution transport."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.worker.remote.protocol import (
    MAX_FRAME_BYTES,
    AuthenticationError,
    ControlEnvelope,
    FragmentAssembler,
    Hello,
    ProtocolError,
    sign_hello,
    verify_hello,
    fragment,
    EXECUTION_HANDLERS,
    LEGACY_EXECUTION_HANDLERS,
    hello_execution_handlers,
)


def test_handler_negotiation_keeps_old_launchers_out_of_summary_claims() -> None:
    assert hello_execution_handlers({}) == LEGACY_EXECUTION_HANDLERS
    assert hello_execution_handlers({"handlers": [list(pair) for pair in EXECUTION_HANDLERS], "summary_claim_protocol": 1}) == EXECUTION_HANDLERS
    assert hello_execution_handlers({"handlers": [list(pair) for pair in EXECUTION_HANDLERS]}) == LEGACY_EXECUTION_HANDLERS
    assert hello_execution_handlers({"handlers": [["agent.summarize", 2], ["agent.run", 1]]}) == (("agent.run", 1),)


@pytest.mark.parametrize("handlers", [None, "all", [["agent.run", True]], [["agent.run"]], [["agent.run", 1]] * 2, [["agent.run", 1]] * 65])
def test_invalid_handler_advertisements_are_rejected(handlers) -> None:
    with pytest.raises(ProtocolError):
        hello_execution_handlers({"handlers": handlers})


def _hello(**changes: object) -> Hello:
    values: dict[str, object] = {
        "launcher_id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "incarnation_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "job_uid": "job-uid",
        "pod_uid": "pod-uid",
        "hostname": "runner-0",
        "form": "job",
        "issued_at": 1_700_000_000,
        "nonce": "n" * 32,
        "hello_sequence": 1,
    }
    values.update(changes)
    return Hello(**values)


def test_hello_signature_binds_every_identity_field() -> None:
    hello = _hello()
    signed = sign_hello("platform-secret", hello)

    assert verify_hello("platform-secret", signed, now=hello.issued_at + 1) == hello

    tampered = replace(signed, pod_uid="other-pod")
    with pytest.raises(AuthenticationError):
        verify_hello("platform-secret", tampered, now=hello.issued_at + 1)


def test_external_hello_signature_binds_the_prepared_resource() -> None:
    hello = _hello(form="external", job_uid=None, pod_uid=None, resource_uid="resource-123")
    signed = sign_hello("platform-secret", hello)
    assert verify_hello("platform-secret", signed, now=hello.issued_at) == hello
    with pytest.raises(AuthenticationError):
        verify_hello("platform-secret", replace(signed, resource_uid="replacement"), now=hello.issued_at)


@pytest.mark.parametrize("form", ["job", "static"])
def test_existing_hello_bytes_and_fields_are_unchanged(form) -> None:
    hello = _hello(form=form, **({"job_uid": None, "pod_uid": None} if form == "static" else {}))
    fields = {
        key: str(value) if isinstance(value, UUID) else value
        for key in (
            "launcher_id", "incarnation_id", "job_uid", "pod_uid", "hostname",
            "form", "issued_at", "nonce", "hello_sequence",
        )
        for value in (getattr(hello, key),)
    }
    assert hello.canonical_bytes() == json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    assert "resource_uid" not in sign_hello("platform-secret", hello).to_dict()


def test_hello_rejects_stale_proof_before_database_registration() -> None:
    hello = _hello()
    signed = sign_hello("platform-secret", hello)

    with pytest.raises(AuthenticationError, match="expired"):
        verify_hello("platform-secret", signed, now=hello.issued_at + 61)


def test_hello_signature_binds_monotonic_replay_sequence() -> None:
    hello = _hello(hello_sequence=2)
    signed = sign_hello("platform-secret", hello)

    with pytest.raises(AuthenticationError):
        verify_hello("platform-secret", replace(signed, hello_sequence=3), now=hello.issued_at)


def test_envelope_rejects_oversize_frames_before_decoding_json() -> None:
    with pytest.raises(ProtocolError, match="frame"):
        ControlEnvelope.from_json("x" * (MAX_FRAME_BYTES + 1))


def test_envelope_round_trips_only_json_object_payloads() -> None:
    envelope = ControlEnvelope(
        session_generation=7,
        sequence=3,
        kind="result",
        payload={"route_id": "route-1", "result": {"success": True}},
    )

    decoded = ControlEnvelope.from_json(envelope.to_json())

    assert decoded == envelope
    with pytest.raises(ProtocolError, match="object"):
        ControlEnvelope.from_json(json.dumps(["not", "an", "object"]))


def test_envelope_uses_the_execution_result_json_contract_for_non_json_values() -> None:
    created_at = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)
    identifier = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    envelope = ControlEnvelope(
        session_generation=7,
        sequence=3,
        kind="result",
        payload={
            "result": {
                "metadata": {"created_at": created_at, "day": date(2026, 10, 25)},
                "identifier": identifier,
                "amount": Decimal("12.34"),
            },
        },
    )

    decoded = ControlEnvelope.from_json(envelope.to_json())

    assert decoded.payload == {
        "result": {
            "metadata": {"created_at": str(created_at), "day": "2026-10-25"},
            "identifier": str(identifier),
            "amount": "12.34",
        },
    }


def test_large_logical_message_round_trips_in_bounded_ordered_frames() -> None:
    created_at = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)
    envelope = ControlEnvelope(
        session_generation=1,
        sequence=1,
        kind="command",
        payload={
            "context": "x" * (MAX_FRAME_BYTES * 2),
            "result": {"metadata": {"created_at": created_at}},
        },
    )

    frames = list(fragment(envelope))
    assembler = FragmentAssembler()

    assert len(frames) > 1
    assert all(len(frame.to_json().encode()) <= MAX_FRAME_BYTES for frame in frames)
    received = [assembler.feed(frame) for frame in frames]
    assert received[:-1] == [None] * (len(frames) - 1)
    assert received[-1] == ControlEnvelope(
        session_generation=1,
        sequence=1,
        kind="command",
        payload={
            "context": "x" * (MAX_FRAME_BYTES * 2),
            "result": {"metadata": {"created_at": str(created_at)}},
        },
    )
