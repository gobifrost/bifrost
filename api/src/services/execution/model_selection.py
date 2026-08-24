"""Dynamic profile resolution for summarization + tuning."""
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.llm import BaseLLMClient, get_llm_client


async def get_summarization_client(db: AsyncSession) -> tuple[BaseLLMClient, str]:
    """Return ``(llm_client, resolved_model_name)`` for summarization calls."""
    try:
        client = await get_llm_client(db, assignment_key="summarization")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return client, client.config.model


async def get_tuning_client(db: AsyncSession) -> tuple[BaseLLMClient, str]:
    """Return ``(llm_client, resolved_model_name)`` for tuning + dry-run calls."""
    try:
        client = await get_llm_client(db, assignment_key="tuning")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return client, client.config.model
