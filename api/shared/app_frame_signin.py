"""Instance-wide framing policy and one-use handoff for app sign-in."""

from base64 import urlsafe_b64encode
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import AppFrameSigninSettings
from src.core.cache import get_shared_redis
from src.models.orm import SystemConfig

_SETTINGS_ID = UUID("650e019b-88a0-4b53-b36e-f4a4adeb2012")
_CODE_TTL_SECONDS = 60


async def get_app_frame_signin_settings(db: AsyncSession) -> AppFrameSigninSettings:
    config = await db.get(SystemConfig, _SETTINGS_ID)
    return AppFrameSigninSettings.model_validate(config.value_json if config else {})


async def save_app_frame_signin_settings(
    db: AsyncSession, settings: AppFrameSigninSettings, actor: str,
) -> None:
    statement = insert(SystemConfig).values(
        id=_SETTINGS_ID,
        category="app_embedding",
        key="frame_signin",
        value_json=settings.model_dump(),
        created_by=actor,
        updated_by=actor,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[SystemConfig.id],
        set_={
            "value_json": statement.excluded.value_json,
            "updated_at": func.now(),
            "updated_by": actor,
        },
    ))


def _code_key(code: str) -> str:
    return "auth:app_frame_signin:" + sha256(code.encode()).hexdigest()


async def issue_app_frame_signin_code(challenge: str, user_id: UUID, session_id: UUID) -> None:
    redis = await get_shared_redis()
    await redis.set(_code_key(challenge), f"{user_id}:{session_id}", ex=_CODE_TTL_SECONDS, nx=True)


async def consume_app_frame_signin_code(code: str) -> tuple[UUID, UUID] | None:
    redis = await get_shared_redis()
    challenge = urlsafe_b64encode(sha256(code.encode()).digest()).decode().rstrip("=")
    value = await redis.getdel(_code_key(challenge))
    if value is None:
        return None
    user_id, session_id = (value.decode() if isinstance(value, bytes) else value).split(":")
    return UUID(user_id), UUID(session_id)
