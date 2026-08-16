"""
AI SDK for Bifrost.

Provides Python API for LLM completions using platform-configured providers.
Supports structured outputs via Pydantic models and optional RAG integration.

All methods are async and must be awaited.

Usage:
    from bifrost import ai

    # Simple completion
    response = await ai.complete("Summarize this: ...")
    print(response.content)

    # With messages
    response = await ai.complete(messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello!"},
    ])

    # Structured output with Pydantic
    from pydantic import BaseModel

    class Summary(BaseModel):
        title: str
        points: list[str]

    result = await ai.complete(
        "Summarize this article...",
        response_format=Summary
    )
    print(result.title)  # Typed!

    # With RAG context (searches knowledge before completion)
    response = await ai.complete(
        "What are our refund policies?",
        knowledge=["policies", "faq"]
    )

    # Streaming
    async for chunk in ai.stream("Write a story..."):
        print(chunk.content, end="")
"""

from __future__ import annotations

import json
import logging
import base64
import re
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

from pydantic import BaseModel

from .client import get_client
from .models import AIInputFile, AIResponse, AIStreamChunk, ArtifactRef

logger = logging.getLogger(__name__)

# Type variable for structured outputs
T = TypeVar("T", bound=BaseModel)


def _default_media_filename(prompt: str, fallback: str) -> str:
    """Derive a short human filename while the platform owns final casing."""
    words = re.findall(r"[A-Za-z0-9]+", prompt)[:6]
    return " ".join(words) if words else fallback


async def _encode_input_files(
    input_files: list[AIInputFile | ArtifactRef | dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Resolve portable artifact refs and encode bounded provider inputs."""
    if not input_files:
        return []
    if len(input_files) > 5:
        raise ValueError("Attach no more than 5 files to one AI request.")

    from .artifacts import artifacts

    encoded: list[dict[str, str]] = []
    for item in input_files:
        if isinstance(item, AIInputFile):
            file_input = item
        else:
            artifact = item if isinstance(item, ArtifactRef) else ArtifactRef.model_validate(item)
            data = await artifacts.read(artifact)
            file_input = AIInputFile(
                filename=artifact.filename,
                content_type=artifact.content_type,
                data=data,
            )
        if len(file_input.data) > 25 * 1024 * 1024:
            raise ValueError(f"{file_input.filename} is too large (maximum 25 MB).")
        encoded.append(
            {
                "filename": file_input.filename,
                "content_type": file_input.content_type,
                "data_base64": base64.b64encode(file_input.data).decode("ascii"),
            }
        )
    return encoded


def _build_messages(
    prompt: str | None,
    messages: list[dict[str, str]] | None,
    system: str | None,
) -> list[dict[str, str]]:
    """
    Build message list from various input formats.

    Args:
        prompt: Simple string prompt (becomes user message)
        messages: Pre-formatted message list
        system: System prompt to prepend

    Returns:
        List of message dicts with role and content
    """
    result: list[dict[str, str]] = []

    # Add system prompt if provided
    if system:
        result.append({"role": "system", "content": system})

    # Add messages or prompt
    if messages:
        # Filter out system messages if we already added one
        for msg in messages:
            if system and msg.get("role") == "system":
                continue
            result.append(msg)
    elif prompt:
        result.append({"role": "user", "content": prompt})

    return result


async def _inject_knowledge_context(
    messages: list[dict[str, str]],
    knowledge: list[str],
    org_id: str | None,
) -> list[dict[str, str]]:
    """
    Search knowledge namespaces and inject context into messages.

    Prepends relevant knowledge as a system message.
    """
    from . import knowledge as knowledge_module

    # Extract the user's question from the last user message
    user_query = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_query = msg.get("content")
            break

    if not user_query:
        return messages

    # Search knowledge
    results = await knowledge_module.search(
        user_query,
        namespace=knowledge,
        scope=org_id,
        limit=5,
    )

    if not results:
        return messages

    # Build context from results
    context_parts = ["Relevant context from knowledge base:"]
    for doc in results:
        context_parts.append(f"\n---\n{doc.content}")

    knowledge_context = "\n".join(context_parts)

    # Find or create system message
    result = messages.copy()
    system_idx = next(
        (i for i, m in enumerate(result) if m.get("role") == "system"),
        None
    )

    if system_idx is not None:
        # Append to existing system message
        current = result[system_idx].get("content", "")
        result[system_idx] = {
            "role": "system",
            "content": f"{current}\n\n{knowledge_context}"
        }
    else:
        # Prepend new system message
        result.insert(0, {
            "role": "system",
            "content": knowledge_context
        })

    return result


def _build_structured_prompt(
    messages: list[dict[str, str]],
    response_format: type[BaseModel],
) -> list[dict[str, str]]:
    """
    Modify messages to request structured JSON output.

    Appends JSON schema instructions to the system message.
    """
    schema = response_format.model_json_schema()
    schema_str = json.dumps(schema, indent=2)

    instruction = (
        f"\n\nYou must respond with valid JSON matching this schema:\n"
        f"```json\n{schema_str}\n```\n"
        f"Respond ONLY with the JSON object, no additional text."
    )

    result = messages.copy()
    system_idx = next(
        (i for i, m in enumerate(result) if m.get("role") == "system"),
        None
    )

    if system_idx is not None:
        current = result[system_idx].get("content", "")
        result[system_idx] = {
            "role": "system",
            "content": f"{current}{instruction}"
        }
    else:
        result.insert(0, {
            "role": "system",
            "content": instruction
        })

    return result


def _parse_structured_response(
    content: str,
    response_format: type[T],
) -> T:
    """
    Parse LLM response into Pydantic model.

    Handles JSON extraction from markdown code blocks if present.
    """
    # Try to extract JSON from markdown code block
    text = content.strip()
    if text.startswith("```"):
        # Find the end of the code block
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            elif line.startswith("```") and in_block:
                break
            elif in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)

    # Parse and validate
    data = json.loads(text)
    return response_format.model_validate(data)


class ai:
    """
    AI completion operations.

    Provides LLM completions using platform-configured providers.
    Supports structured outputs and RAG integration.
    """

    @staticmethod
    async def create_image(
        prompt: str,
        *,
        filename: str | None = None,
    ) -> ArtifactRef:
        """Generate an image and return one portable artifact reference."""
        from .artifacts import artifacts

        return await artifacts.create_image(
            filename or _default_media_filename(prompt, "Generated Image"),
            prompt=prompt,
        )

    @staticmethod
    async def create_video(
        prompt: str,
        *,
        filename: str | None = None,
        timeout_seconds: float = 1_800,
        poll_interval_seconds: float = 2,
    ) -> ArtifactRef:
        """Generate a durable video and return one portable artifact reference."""
        from .artifacts import artifacts

        return await artifacts.create_video(
            filename or _default_media_filename(prompt, "Generated Video"),
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    @staticmethod
    async def complete(
        prompt: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        system: str | None = None,
        response_format: type[T] | None = None,
        knowledge: list[str] | None = None,
        max_tokens: int | None = None,
        org_id: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        files: list[AIInputFile | ArtifactRef | dict[str, Any]] | None = None,
    ) -> AIResponse | T:
        """
        Generate an AI completion.

        Can be called with a simple prompt or a list of messages.
        Optionally returns structured output as a Pydantic model.

        Args:
            prompt: Simple text prompt (becomes a user message)
            messages: List of message dicts with "role" and "content"
            system: System prompt (prepended to messages)
            response_format: Pydantic model class for structured output
            knowledge: List of knowledge namespace(s) to search for context
            max_tokens: Override default max tokens
            org_id: Organization scope for knowledge search
            model: Override default model (must be compatible with configured provider)
            timeout: Override default HTTP timeout in seconds (default: 30s)
            files: Up to five binary inputs or portable ArtifactRef objects.

        Returns:
            AIResponse with content, or parsed Pydantic model if response_format provided

        Example:
            >>> from bifrost import ai
            >>> response = await ai.complete("Hello!")
            >>> print(response.content)

            >>> # Structured output
            >>> from pydantic import BaseModel
            >>> class Answer(BaseModel):
            ...     answer: str
            ...     confidence: float
            >>> result = await ai.complete(
            ...     "What is 2+2?",
            ...     response_format=Answer
            ... )
            >>> print(result.answer, result.confidence)

            >>> # Use a different model
            >>> response = await ai.complete(
            ...     "Complex reasoning task...",
            ...     model="gpt-4o"
            ... )
        """
        if prompt is None and messages is None:
            raise ValueError("Either 'prompt' or 'messages' must be provided")

        # Build message list
        msg_list = _build_messages(prompt, messages, system)

        # Inject knowledge context if requested
        if knowledge:
            msg_list = await _inject_knowledge_context(msg_list, knowledge, org_id)

        # Add structured output instructions
        if response_format:
            msg_list = _build_structured_prompt(msg_list, response_format)

        # Get execution context for usage tracking
        from ._context import _execution_context

        ctx = _execution_context.get()
        execution_id = str(ctx.execution_id) if ctx and ctx.execution_id else None

        # Call API
        client = get_client()
        response = await client.post(
            "/api/sdk/ai/complete",
            json={
                "messages": msg_list,
                "max_tokens": max_tokens,
                "org_id": org_id,
                "model": model,
                "execution_id": execution_id,
                "input_files": await _encode_input_files(files),
            },
            timeout=timeout,
        )
        if not response.is_success:
            # Extract error detail from response if available
            try:
                error_data = response.json()
                error_msg = error_data.get("detail", response.text)
            except Exception:
                error_msg = response.text or f"HTTP {response.status_code}"
            raise RuntimeError(f"AI completion failed: {error_msg}")
        data = response.json()

        # Parse structured response if requested
        if response_format and data.get("content"):
            return _parse_structured_response(data["content"], response_format)

        return AIResponse(
            content=data.get("content") or "",
            input_tokens=data.get("input_tokens") or 0,
            output_tokens=data.get("output_tokens") or 0,
            model=data.get("model") or "",
        )

    @staticmethod
    async def stream(
        prompt: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        system: str | None = None,
        knowledge: list[str] | None = None,
        max_tokens: int | None = None,
        org_id: str | None = None,
        model: str | None = None,
        files: list[AIInputFile | ArtifactRef | dict[str, Any]] | None = None,
    ) -> AsyncGenerator[AIStreamChunk, None]:
        """
        Generate a streaming AI completion.

        Yields chunks as they arrive from the LLM.

        Args:
            prompt: Simple text prompt (becomes a user message)
            messages: List of message dicts with "role" and "content"
            system: System prompt (prepended to messages)
            knowledge: List of knowledge namespace(s) to search for context
            max_tokens: Override default max tokens
            org_id: Organization scope for knowledge search
            model: Override default model (must be compatible with configured provider)
            files: Up to five binary inputs or portable ArtifactRef objects.

        Yields:
            AIStreamChunk objects with content deltas

        Example:
            >>> from bifrost import ai
            >>> async for chunk in ai.stream("Write a story..."):
            ...     if chunk.content:
            ...         print(chunk.content, end="", flush=True)
            ...     if chunk.done:
            ...         print(f"\\nTokens: {chunk.input_tokens}/{chunk.output_tokens}")

            >>> # Use a different model
            >>> async for chunk in ai.stream("Write a story...", model="gpt-4o"):
            ...     print(chunk.content, end="")
        """
        if prompt is None and messages is None:
            raise ValueError("Either 'prompt' or 'messages' must be provided")

        # Build message list
        msg_list = _build_messages(prompt, messages, system)

        # Inject knowledge context if requested
        if knowledge:
            msg_list = await _inject_knowledge_context(msg_list, knowledge, org_id)

        # Get execution context for usage tracking
        from ._context import _execution_context

        ctx = _execution_context.get()
        execution_id = str(ctx.execution_id) if ctx and ctx.execution_id else None

        # Call API with SSE streaming
        client = get_client()
        async with client.stream(
            "POST",
            "/api/sdk/ai/stream",
            json={
                "messages": msg_list,
                "max_tokens": max_tokens,
                "org_id": org_id,
                "model": model,
                "execution_id": execution_id,
                "input_files": await _encode_input_files(files),
            }
        ) as response:
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue

                data_str = line[6:]  # Remove "data: " prefix
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                    if data.get("done"):
                        yield AIStreamChunk(
                            content="",
                            done=True,
                            input_tokens=data.get("input_tokens"),
                            output_tokens=data.get("output_tokens"),
                        )
                    else:
                        yield AIStreamChunk(
                            content=data.get("content") or "",
                            done=False,
                        )
                except json.JSONDecodeError:
                    continue

    @staticmethod
    async def get_model_info() -> dict[str, Any]:
        """
        Get information about the configured LLM.

        Returns:
            Dict with provider, model, and configuration details

        Example:
            >>> info = await ai.get_model_info()
            >>> print(f"Using {info['provider']}/{info['model']}")
        """
        client = get_client()
        response = await client.get("/api/sdk/ai/info")
        if not response.is_success:
            # Extract error detail from response if available
            try:
                error_data = response.json()
                error_msg = error_data.get("detail", response.text)
            except Exception:
                error_msg = response.text or f"HTTP {response.status_code}"
            raise RuntimeError(f"Failed to get AI model info: {error_msg}")
        return response.json()
