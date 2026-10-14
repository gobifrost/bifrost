"""Persist the latest Chat request's measurements independently of usage totals."""

from uuid import UUID

from sqlalchemy import cast, func, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.agents import ChatContextUsage
from src.models.orm.agents import Conversation


async def store_chat_context_usage(
    session: AsyncSession,
    conversation_id: UUID,
    usage: ChatContextUsage,
) -> None:
    """Replace only the context snapshot, preserving other channel metadata."""

    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            extra_data=func.coalesce(Conversation.extra_data, cast({}, JSONB)).op("||")(
                cast({"context_usage": usage.model_dump(mode="json")}, JSONB)
            ),
            updated_at=Conversation.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
