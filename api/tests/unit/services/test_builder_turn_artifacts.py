from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from src.services.builder.turn_artifacts import (
    BuilderHarnessStateStorage,
    BuilderHarnessStateTooLarge,
    BuilderTurnArtifactStorage,
)


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


async def test_harness_state_is_staged_then_promoted_to_immutable_turn_key() -> None:
    solution_id = uuid4()
    session_id = uuid4()
    turn_id = uuid4()
    payload = b"PK\x05\x06" + b"\x00" * 18
    storage = BuilderHarnessStateStorage()

    digest, size = await storage.write_staged(
        turn_id,
        2,
        _chunks(payload[:10], payload[10:]),
        max_bytes=1024,
    )
    await storage.promote(
        solution_id=solution_id,
        session_id=session_id,
        turn_id=turn_id,
        dispatch_attempt=2,
    )

    restored = b"".join(
        [
            chunk
            async for chunk in storage.iter_accepted(
                solution_id,
                session_id,
                turn_id,
            )
        ]
    )
    assert digest == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)
    assert restored == payload
    assert await storage.exists_accepted(solution_id, session_id, turn_id)

    await storage.delete_staged(turn_id, 2)
    await storage.delete_accepted(solution_id, session_id, turn_id)


async def test_harness_state_rejects_oversized_upload() -> None:
    storage = BuilderHarnessStateStorage()
    turn_id = uuid4()

    with pytest.raises(BuilderHarnessStateTooLarge, match="exceeds"):
        await storage.write_staged(
            turn_id,
            1,
            _chunks(b"too large"),
            max_bytes=3,
        )

    await storage.delete_staged(turn_id, 1)


async def test_failed_turn_workspace_is_promoted_to_an_inert_checkpoint() -> None:
    solution_id = uuid4()
    session_id = uuid4()
    turn_id = uuid4()
    payload = b"PK\x05\x06" + b"\x00" * 18
    storage = BuilderTurnArtifactStorage(turn_id, 3)

    digest, _size = await storage.write_output(
        _chunks(payload),
        max_bytes=1024,
    )
    await storage.promote_checkpoint(
        solution_id=solution_id,
        session_id=session_id,
    )

    restored = b"".join(
        [
            chunk
            async for chunk in storage.iter_checkpoint(
                solution_id,
                session_id,
                turn_id,
            )
        ]
    )
    assert digest == hashlib.sha256(payload).hexdigest()
    assert restored == payload

    await storage.delete()
    await storage.delete_checkpoint(solution_id, session_id, turn_id)
