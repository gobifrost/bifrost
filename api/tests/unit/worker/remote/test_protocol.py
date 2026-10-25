"""Wire contracts for the production remote execution transport."""

from __future__ import annotations

import json
from dataclasses import replace
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
)


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


def test_large_logical_message_round_trips_in_bounded_ordered_frames() -> None:
    envelope = ControlEnvelope(
        session_generation=1,
        sequence=1,
        kind="command",
        payload={"context": "x" * (MAX_FRAME_BYTES * 2)},
    )

    frames = list(fragment(envelope))
    assembler = FragmentAssembler()

    assert len(frames) > 1
    assert all(len(frame.to_json().encode()) <= MAX_FRAME_BYTES for frame in frames)
    received = [assembler.feed(frame) for frame in frames]
    assert received[:-1] == [None] * (len(frames) - 1)
    assert received[-1] == envelope
