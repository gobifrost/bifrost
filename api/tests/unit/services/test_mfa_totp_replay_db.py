"""Postgres-backed TOTP replay coverage.

These tests use independent sessions so they exercise the persisted counter and
the row lock, not just in-memory comparison.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pyotp
import pytest
from sqlalchemy import delete, select

from src.core.security import encrypt_secret
from src.models.orm.mfa import MFAMethodStatus, MFAMethodType, UserMFAMethod
from src.models.orm.users import User
from src.services.mfa_service import MFAService


async def _seed_method(
    session,
    *,
    status: MFAMethodStatus,
) -> tuple[User, str, UserMFAMethod]:
    secret = pyotp.random_base32()
    user = User(
        id=uuid4(),
        email=f"totp-replay-{uuid4().hex[:8]}@example.com",
        name="TOTP Replay",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_registered=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    method = UserMFAMethod(
        id=uuid4(),
        user_id=user.id,
        method_type=MFAMethodType.TOTP,
        status=status,
        encrypted_secret=encrypt_secret(secret),
    )
    session.add(user)
    await session.flush()
    session.add(method)
    await session.commit()
    return user, secret, method


async def _cleanup(session_factory, user_id) -> None:
    async with session_factory() as cleanup:
        await cleanup.execute(delete(User).where(User.id == user_id))
        await cleanup.commit()


@pytest.mark.asyncio
async def test_accepted_code_is_rejected_from_a_new_transaction(
    db_session, async_session_factory
):
    user, secret, _method = await _seed_method(
        db_session, status=MFAMethodStatus.ACTIVE
    )
    code = pyotp.TOTP(secret).now()

    try:
        async with async_session_factory() as session:
            assert await MFAService(session).verify_totp_code(user.id, code) is True
            await session.commit()

        async with async_session_factory() as session:
            assert await MFAService(session).verify_totp_code(user.id, code) is False
            await session.rollback()
    finally:
        await _cleanup(async_session_factory, user.id)


@pytest.mark.asyncio
async def test_concurrent_same_code_authenticates_exactly_once(
    db_session, async_session_factory
):
    user, secret, _method = await _seed_method(
        db_session, status=MFAMethodStatus.ACTIVE
    )
    code = pyotp.TOTP(secret).now()

    async def attempt() -> bool:
        async with async_session_factory() as session:
            accepted = await MFAService(session).verify_totp_code(user.id, code)
            if accepted:
                await session.commit()
            else:
                await session.rollback()
            return accepted

    try:
        results = await asyncio.gather(attempt(), attempt())
        assert sorted(results) == [False, True]
    finally:
        await _cleanup(async_session_factory, user.id)


@pytest.mark.asyncio
async def test_enrollment_consumes_the_code_step(db_session, async_session_factory):
    user, secret, method = await _seed_method(
        db_session, status=MFAMethodStatus.PENDING
    )
    code = pyotp.TOTP(secret).now()

    try:
        async with async_session_factory() as session:
            service = MFAService(session)
            db_user = await session.get(User, user.id)
            assert db_user is not None
            codes = await service.verify_totp_enrollment(db_user, code)
            assert codes
            await session.commit()

        async with async_session_factory() as session:
            stored = (
                await session.execute(
                    select(UserMFAMethod).where(UserMFAMethod.id == method.id)
                )
            ).scalar_one()
            assert stored.last_used_counter is not None
            assert stored.status == MFAMethodStatus.ACTIVE

        async with async_session_factory() as session:
            assert await MFAService(session).verify_totp_code(user.id, code) is False
            await session.rollback()
    finally:
        await _cleanup(async_session_factory, user.id)
