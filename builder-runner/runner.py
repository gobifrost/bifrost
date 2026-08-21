#!/usr/bin/env python3
"""Cloudflare-hosted executor for Bifrost's shared Pydantic AI runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.usage import RunUsage
from src.models.contracts.agents import ToolCall
from shared.sandbox_runner_protocol import (
    SandboxBuilderToolResponse,
    SandboxBuilderTurnContext,
    SandboxBuilderWorkspaceBuildRequest,
    SandboxBuilderWorkspaceBuildResult,
)
from shared.builder_workspace_archive import hydrate_builder_turn_workspace

from src.services.agent_runtime import (
    AgentRunBudget,
    AgentRuntimeRunner,
    AgentTurnCoordinator,
    AssistantSegmentResult,
    ModelCallEvent,
    ToolExecutionResult,
    ToolStartResult,
    agent_model_settings,
    create_agent_model,
)
from src.services.agent_runtime.history import build_runtime_message_history
from src.services.agent_runtime.usage_governance import (
    runtime_usage_governance_from_snapshot,
    runtime_usage_subject,
)
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.builder.local_app_build import (
    LocalBuildCancelled,
    materialize_build_input,
    run_local_app_build,
)
from src.services.builder.scaffold import zip_workspace
from src.services.builder.workspace_tool_runtime import (
    CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID,
    TEST_SOLUTION_BUILD_TOOL_ID,
    execute_builder_workspace_tool,
)
from src.services.llm.base import LLMConfig, LLMInputFile, ToolDefinition
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.agent_runtime.model_factory import provider_name_for_config

SCHEMA_VERSION = 1
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_CALLBACK_BODY_BYTES = 32 * 1024 * 1024
CALLBACK_ATTEMPTS = 3
CALLBACK_USER_AGENT = "Bifrost-Build/1.0"
REPORTED_FAILURE_EXIT = 1
REPORTED_CANCELLED_EXIT = 2
CALLBACK_FAILURE_EXIT = 3
WORKSPACE_COMMAND_MAX_ARGS = 64
WORKSPACE_COMMAND_MAX_ARG_BYTES = 4096
WORKSPACE_COMMAND_MAX_OUTPUT_BYTES = 64 * 1024
WORKSPACE_COMMAND_MAX_TIMEOUT_SECONDS = 60
BROKER_SETUP_ATTEMPTS = 3


class RunnerError(RuntimeError):
    pass


class Cancelled(RunnerError):
    pass


def _retryable_broker_setup_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429} or (
            exc.response.status_code >= 500
        )
    return isinstance(exc, (httpx.TransportError, TimeoutError))


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    job_id: UUID
    job_type: Literal["solution.build", "solution.builder.turn"]
    dispatch_attempt: int = Field(ge=1)
    callback_base_url: str
    capability: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: int = Field(ge=1, le=MAX_TIMEOUT_SECONDS)
    runner_sandbox_id: str | None = Field(default=None, min_length=1, max_length=128)
    workspace_sandbox_id: str | None = Field(default=None, min_length=1, max_length=128)
    workspace_broker_url: str | None = Field(default=None, min_length=1, max_length=500)
    runner_allowed_hosts: list[str] = Field(default_factory=list, max_length=32)
    workspace_allowed_hosts: list[str] = Field(default_factory=list, max_length=32)

    def callback_url(self) -> str:
        value = self.callback_base_url.rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise RunnerError("callback_base_url must be an absolute HTTP(S) URL")
        return value + f"/api/internal/sandbox/jobs/{self.job_id}"

    def broker_url(self) -> str | None:
        if self.workspace_broker_url is None:
            return None
        value = self.workspace_broker_url.rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise RunnerError("workspace_broker_url must be an absolute HTTP(S) URL")
        return value


class CallbackClient:
    def __init__(self, envelope: Envelope) -> None:
        self.envelope = envelope
        self.base = envelope.callback_url()
        self.headers = {
            "Authorization": f"Bearer {envelope.capability}",
            "User-Agent": CALLBACK_USER_AGENT,
        }
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=20),
            headers=self.headers,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "CallbackClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        timeout: float | None = None,
        attempts: int = CALLBACK_ATTEMPTS,
    ) -> httpx.Response:
        error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                response = await self.http.request(
                    method,
                    self.base + path,
                    json=json_body,
                    content=content,
                    timeout=timeout,
                )
                response.raise_for_status()
                if len(response.content) > MAX_CALLBACK_BODY_BYTES:
                    raise RunnerError("callback response exceeds the runner limit")
                return response
            except httpx.HTTPStatusError as exc:
                error = exc
                retryable = exc.response.status_code in {408, 429} or (
                    exc.response.status_code >= 500
                )
                if not retryable:
                    break
            except (httpx.HTTPError, TimeoutError) as exc:
                error = exc
            if attempt + 1 < max(1, attempts):
                await asyncio.sleep(2**attempt)
        detail = ""
        if isinstance(error, httpx.HTTPStatusError):
            detail = error.response.text[:4000]
        raise RunnerError(
            f"callback {method} {path} failed"
            + (f": {detail}" if detail else "")
        ) from error

    async def json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        response = await self.request(
            method,
            path,
            json_body=body,
            timeout=timeout,
        )
        if not response.content:
            return {}
        value = response.json()
        if not isinstance(value, dict):
            raise RunnerError(f"callback {path} returned non-object JSON")
        return value

    @asynccontextmanager
    async def stream(self, path: str) -> AsyncIterator[httpx.Response]:
        async with self.http.stream("GET", self.base + path, timeout=600) as response:
            response.raise_for_status()
            yield response

    async def progress(
        self,
        phase: str,
        current: int = 0,
        total: int | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "phase": phase[:200],
            "current": max(0, current),
        }
        if total is not None:
            body["total"] = max(0, total)
            body["percent"] = (
                100.0 if total == 0 else min(100.0, current / total * 100)
            )
        try:
            await self.request("POST", "/progress", json_body=body, timeout=15)
        except RunnerError as exc:
            print(f"Transient progress callback failure: {exc}", file=sys.stderr)

    async def cancelled(self) -> bool:
        try:
            value = await self.json("GET", "/cancelled", timeout=10)
        except RunnerError as exc:
            print(f"Transient cancellation callback failure: {exc}", file=sys.stderr)
            return False
        return value.get("cancelled") is True

    async def ensure_not_cancelled(self) -> None:
        if await self.cancelled():
            raise Cancelled("Builder job was cancelled")

    async def events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        await self.request(
            "POST",
            "/events",
            json_body={"events": events},
            timeout=30,
        )

    async def complete(self, body: dict[str, Any]) -> None:
        await self.request("POST", "/complete", json_body=body, timeout=120)


class CallbackBuildStorage:
    """Duck-typed storage adapter for the canonical app build implementation."""

    def __init__(self, client: CallbackClient) -> None:
        self.client = client

    async def open_input_stream(self) -> AsyncIterator[bytes]:
        async with self.client.stream("/input") as response:
            async for chunk in response.aiter_bytes(1024 * 1024):
                yield chunk

    async def write_output(
        self,
        _app_id: UUID,
        rel_path: str,
        chunks: AsyncIterator[bytes],
        _max_bytes: int,
    ) -> tuple[str, int]:
        response = await self.client.request(
            "PUT",
            "/artifacts/" + quote(rel_path, safe="/"),
            content=chunks,
            timeout=120,
            attempts=1,
        )
        value = response.json()
        return str(value["sha256"]), int(value["size"])


async def _put_file(client: CallbackClient, path: str, source: Path) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk

    await client.request("PUT", path, content=chunks(), timeout=120, attempts=1)


class WorkspaceBrokerClient:
    """Client for the Worker-internal runner-to-workspace sandbox bridge."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=20),
            follow_redirects=False,
        )

    async def __aenter__(self) -> "WorkspaceBrokerClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        response = await self.http.request(
            method,
            self.base_url + path,
            json=json_body,
            content=content,
            timeout=timeout,
        )
        response.raise_for_status()
        if len(response.content) > MAX_CALLBACK_BODY_BYTES:
            raise RunnerError("workspace broker response exceeds the runner limit")
        return response

    async def configure_runner_egress(self, hosts: list[str]) -> None:
        await self._request(
            "POST",
            "/runner-egress",
            json_body={"allowed_hosts": sorted(set(hosts))},
            timeout=30,
        )

    async def hydrate(
        self,
        chunks: AsyncIterator[bytes],
        *,
        expected_sha256: str,
        solution_id: str,
    ) -> None:
        path = (
            "/hydrate?"
            + f"expected_sha256={quote(expected_sha256)}"
            + f"&solution_id={quote(solution_id)}"
        )
        await self._request(
            "POST",
            path,
            content=chunks,
            timeout=600,
        )

    async def execute_tool(
        self,
        *,
        bundle_path: str | None,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        response = await self._request(
            "POST",
            "/tool",
            json_body={
                "bundle_path": bundle_path,
                "name": name,
                "arguments": arguments,
            },
            timeout=120,
        )
        value = response.json()
        if not isinstance(value, dict):
            raise RunnerError("workspace broker returned non-object tool JSON")
        return value

    async def archive_to_callback(
        self,
        client: CallbackClient,
        output_path: str = "/output",
    ) -> str:
        async with self.http.stream(
            "GET",
            self.base_url + "/archive",
            timeout=600,
        ) as response:
            response.raise_for_status()
            digest = response.headers.get("X-Bifrost-Sha256", "").strip()
            if not digest or len(digest) != 64:
                raise RunnerError("workspace broker archive omitted its SHA-256 digest")
            uploaded = await client.request(
                "PUT",
                output_path,
                content=response.aiter_bytes(1024 * 1024),
                timeout=120,
                attempts=1,
            )
            persisted = uploaded.json()
            if not isinstance(persisted, dict) or persisted.get("sha256") != digest:
                raise RunnerError("workspace archive digest changed during callback upload")
        return digest


def _host_from_http_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return None
    return parsed.hostname


def _model_endpoint_host(config: LLMConfig) -> str | None:
    explicit = _host_from_http_url(config.endpoint)
    if explicit:
        return explicit
    if config.provider == "openai":
        return "api.openai.com"
    if config.provider == "anthropic":
        return "api.anthropic.com"
    if config.provider == "google":
        return "generativelanguage.googleapis.com"
    return None


def _sandbox_compute_ms(started: float, envelope: Envelope) -> int:
    multiplier = 2 if envelope.broker_url() is not None else 1
    return int((time.monotonic() - started) * 1000 * multiplier)


async def run_build(
    client: CallbackClient,
    envelope: Envelope,
    scratch: Path,
) -> None:
    workspace = scratch / "workspace"
    workspace.mkdir(mode=0o700)
    storage = CallbackBuildStorage(client)
    await materialize_build_input(
        storage,  # type: ignore[arg-type]
        workspace,
        expected_sha256=envelope.input_sha256,
    )
    manifest, log_excerpt = await run_local_app_build(
        workspace=workspace,
        storage=storage,  # type: ignore[arg-type]
        app_id=UUID(int=0),
        timeout_seconds=envelope.timeout_seconds,
        log_limit_bytes=128 * 1024,
        output_limit_bytes=100 * 1024 * 1024,
        report=client.progress,
        is_cancelled=client.cancelled,
    )
    await client.complete(
        {
            "status": "succeeded",
            "output_manifest": manifest,
            "log_excerpt": log_excerpt,
        }
    )


async def _download_attachment(
    client: CallbackClient,
    attachment_id: UUID,
    *,
    expected_size: int,
) -> bytes:
    total = 0
    data = bytearray()
    async with client.stream(f"/attachments/{attachment_id}") as response:
        async for chunk in response.aiter_bytes(1024 * 1024):
            total += len(chunk)
            if total > expected_size or total > 25 * 1024 * 1024:
                raise RunnerError("Builder attachment exceeds its declared size")
            data.extend(chunk)
    if total != expected_size:
        raise RunnerError("Builder attachment size does not match its record")
    return bytes(data)


async def _runtime_history(
    client: CallbackClient,
    context: SandboxBuilderTurnContext,
):
    user_inputs: dict[UUID, tuple[str | None, list[LLMInputFile]]] = {}
    for message in context.messages:
        if message.role != "user" or not message.attachments:
            continue
        text_parts = [message.content] if message.content else []
        input_files: list[LLMInputFile] = []
        for attachment in message.attachments:
            if attachment.binary_model_input:
                input_files.append(
                    LLMInputFile(
                        filename=attachment.filename,
                        media_type=attachment.content_type,
                        data=await _download_attachment(
                            client,
                            attachment.id,
                            expected_size=attachment.size_bytes,
                        ),
                    )
                )
            elif attachment.extracted_text:
                text_parts.append(
                    f"[Attached file: {attachment.filename}]\n"
                    f"{attachment.extracted_text}"
                )
        user_inputs[message.id] = (
            "\n\n".join(text_parts) if text_parts else None,
            input_files,
        )
    return build_runtime_message_history(
        system_prompt=context.system_prompt,
        persisted_messages=context.messages,
        user_inputs=user_inputs,
    )


async def _execute_sandbox_tool(
    *,
    workspace: WorkspaceRoot,
    context: SandboxBuilderTurnContext,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    result = await execute_builder_workspace_tool(
        workspace=workspace,
        bundle_path=context.bundle_path,
        name=name,
        arguments=arguments,
    )
    return result.runner_payload()


async def run_turn(
    client: CallbackClient,
    envelope: Envelope,
    scratch: Path,
) -> None:
    sandbox_compute_started = time.monotonic()
    try:
        context = SandboxBuilderTurnContext.model_validate(
            await client.json("GET", "/context")
        )
    except Exception as exc:
        raise RunnerError("Builder runner could not load the turn context") from exc
    llm_config = LLMConfig(
        provider=context.llm_config.provider,
        model=context.llm_config.model,
        api_key=context.llm_config.api_key,
        endpoint=context.llm_config.endpoint,
        max_tokens=context.llm_config.max_tokens,
        extra_params=context.llm_config.extra_params,
    )
    broker_url = envelope.broker_url()
    broker_stack = (
        WorkspaceBrokerClient(broker_url)
        if broker_url is not None
        else None
    )
    workspace_path = scratch / "workspace"
    workspace: WorkspaceRoot | None = None
    async with (
        broker_stack if broker_stack is not None else _null_async_context()
    ) as broker:
        if isinstance(broker, WorkspaceBrokerClient):
            egress_hosts = [
                host
                for host in (
                    _host_from_http_url(envelope.callback_base_url),
                    _model_endpoint_host(llm_config),
                    *envelope.runner_allowed_hosts,
                )
                if host
            ]
            for attempt in range(BROKER_SETUP_ATTEMPTS):
                try:
                    await broker.configure_runner_egress(egress_hosts)
                    break
                except Exception as exc:
                    if (
                        attempt + 1 == BROKER_SETUP_ATTEMPTS
                        or not _retryable_broker_setup_error(exc)
                    ):
                        raise RunnerError(
                            "Builder runner could not configure Cloudflare egress"
                        ) from exc
                    await asyncio.sleep(2**attempt)
            for attempt in range(BROKER_SETUP_ATTEMPTS):
                try:
                    # Reopen the durable callback input on every retry. An
                    # AsyncIterator request body cannot be replayed after a
                    # transient Cloudflare transport disconnect.
                    async with client.stream("/input") as response:
                        await broker.hydrate(
                            response.aiter_bytes(1024 * 1024),
                            expected_sha256=envelope.input_sha256,
                            solution_id=context.solution_id,
                        )
                    break
                except Exception as exc:
                    if (
                        attempt + 1 == BROKER_SETUP_ATTEMPTS
                        or not _retryable_broker_setup_error(exc)
                    ):
                        raise RunnerError(
                            "Builder runner could not hydrate the Cloudflare workspace"
                        ) from exc
                    await asyncio.sleep(2**attempt)
        else:
            workspace_path.mkdir(mode=0o700)
            async with client.stream("/input") as response:
                await hydrate_builder_turn_workspace(
                    response.aiter_bytes(1024 * 1024),
                    destination=workspace_path,
                    expected_sha256=envelope.input_sha256,
                    solution_id=context.solution_id,
                    archive_path=scratch / "input.zip",
                )
            workspace = WorkspaceRoot(workspace_path, WorkspaceLimits())

        await _run_turn_with_workspace(
            client=client,
            envelope=envelope,
            scratch=scratch,
            context=context,
            llm_config=llm_config,
            workspace=workspace,
            broker=broker if isinstance(broker, WorkspaceBrokerClient) else None,
            workspace_path=workspace_path,
            sandbox_compute_started=sandbox_compute_started,
        )


@asynccontextmanager
async def _null_async_context() -> AsyncIterator[None]:
    yield None


async def _run_turn_with_workspace(
    *,
    client: CallbackClient,
    envelope: Envelope,
    scratch: Path,
    context: SandboxBuilderTurnContext,
    llm_config: LLMConfig,
    workspace: WorkspaceRoot | None,
    broker: WorkspaceBrokerClient | None,
    workspace_path: Path,
    sandbox_compute_started: float,
) -> None:
    history = await _runtime_history(client, context)
    history_messages = history[1:]
    if not history_messages or history_messages[-1].role != "user":
        raise RunnerError("Builder conversation has no current user prompt")
    current_prompt: Any = PydanticAIClient.convert_user_content(
        history_messages.pop()
    )
    definitions = [
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
        )
        for tool in context.tools
    ]
    usage = RunUsage()
    budget = AgentRunBudget(
        max_requests=context.max_iterations,
        max_total_tokens=context.max_token_budget,
    )
    usage_governance = runtime_usage_governance_from_snapshot(
        runtime_usage_subject(
            organization_id=None,
            user_id=None,
            solution_id=UUID(context.solution_id),
        ),
        context.runtime_governance,
    )
    model_name = context.llm_config.model
    seen_ids = {
        call.id
        for message in history
        if message.role == "assistant"
        for call in message.tool_calls or []
    }

    async def model_event(event: ModelCallEvent) -> None:
        if event.type == "request":
            await client.progress("AI is working")
        elif event.type == "error":
            await client.progress("AI request failed")

    async def persist_assistant_segment(
        content: str,
        _model: str,
    ) -> AssistantSegmentResult:
        await client.json(
            "POST",
            "/assistant-segments",
            body={"content": content},
        )
        return AssistantSegmentResult()

    async def start_tool(
        tool_call: ToolCall,
        _internal_call_id: str,
    ) -> ToolStartResult:
        await client.progress(f"Using {tool_call.name}")
        started = await client.json(
            "POST",
            "/tools/start",
            body={
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            },
            timeout=envelope.timeout_seconds,
        )
        return ToolStartResult(
            handle=SandboxBuilderToolResponse.model_validate(started),
        )

    async def execute_tool(
        name: str,
        arguments: dict[str, Any],
        _internal_call_id: str,
        _display_call_id: str,
        start_result: ToolStartResult,
    ) -> ToolExecutionResult:
        response = start_result.handle
        if not isinstance(response, SandboxBuilderToolResponse):
            raise RunnerError("Builder tool start returned an invalid handle")
        if response.model_content is not None:
            return ToolExecutionResult(
                model_content=response.model_content,
                error=response.error,
            )
        if response.execution != "sandbox":
            if response.error:
                return ToolExecutionResult(
                    model_content=f"Error: {response.error}",
                    error=response.error,
                )
            raise RunnerError("Bifrost tool callback returned no model content")

        began = time.monotonic()
        try:
            if name == TEST_SOLUTION_BUILD_TOOL_ID:
                workspace_path = (
                    "/tools/"
                    + quote(response.execution_id, safe="")
                    + "/workspace?message_id="
                    + quote(str(response.message_id), safe="")
                )
                if broker is not None:
                    digest = await broker.archive_to_callback(client, workspace_path)
                else:
                    if workspace is None:
                        raise RunnerError("local workspace is unavailable")
                    archive = scratch / f"tool-{response.execution_id}.zip"
                    await asyncio.to_thread(zip_workspace, workspace.root, archive)
                    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                    await _put_file(client, workspace_path, archive)
                checked = await client.json(
                    "POST",
                    "/tools/"
                    + quote(response.execution_id, safe="")
                    + "/workspace-build",
                    body=SandboxBuilderWorkspaceBuildRequest(
                        message_id=response.message_id,
                        output_sha256=digest,
                    ).model_dump(mode="json"),
                    timeout=envelope.timeout_seconds,
                )
                result = SandboxBuilderWorkspaceBuildResult.model_validate(
                    checked
                ).model_dump(mode="json")
            elif broker is not None:
                result = await broker.execute_tool(
                    bundle_path=context.bundle_path,
                    name=name,
                    arguments=arguments,
                )
            else:
                if workspace is None:
                    raise RunnerError("local workspace is unavailable")
                result = await _execute_sandbox_tool(
                    workspace=workspace,
                    context=context,
                    name=name,
                    arguments=arguments,
                )
            error = None
        except Exception as exc:  # noqa: BLE001 - persist model-visible tool error
            result = None
            error = str(exc)
        finished = await client.json(
            "POST",
            "/tools/finish",
            body={
                "message_id": str(response.message_id),
                "execution_id": response.execution_id,
                "result": result,
                "error": error,
                "duration_ms": int((time.monotonic() - began) * 1000),
            },
            timeout=120,
        )
        completion = SandboxBuilderToolResponse.model_validate(finished)
        if completion.model_content is None:
            raise RunnerError("Sandbox tool callback returned no model content")
        return ToolExecutionResult(
            model_content=completion.model_content,
            error=completion.error or error,
        )

    coordinator_ref: dict[str, AgentTurnCoordinator] = {}
    runtime = AgentRuntimeRunner(
        model=create_agent_model(llm_config, model=model_name),
        instructions=context.system_prompt,
        budget=budget,
        model_settings=agent_model_settings(
            llm_config,
            max_tokens=context.llm_config.max_tokens,
            session_id=context.conversation_id,
        ),
        tool_definitions=definitions,
        tool_executor=lambda name, arguments, tool_call_id: coordinator_ref[
            "coordinator"
        ].execute_tool(
            name,
            arguments,
            tool_call_id,
        ),
        model_event_handler=lambda event: coordinator_ref[
            "coordinator"
        ].record_model_event(event),
        compaction_event_handler=lambda before, after: coordinator_ref[
            "coordinator"
        ].record_compaction(
            before,
            after,
        ),
        toolset_id=f"bifrost-builder-{context.solution_id}",
    )
    coordinator_ref["coordinator"] = AgentTurnCoordinator(
        runtime=runtime,
        current_prompt=current_prompt,
        message_history=PydanticAIClient.convert_messages(history_messages),
        usage=usage,
        budget=budget,
        conversation_id=context.conversation_id,
        model_name=model_name,
        assistant_segment_persister=persist_assistant_segment,
        tool_starter=start_tool,
        tool_executor=execute_tool,
        model_event_observer=model_event,
        usage_governance=usage_governance,
        stream=True,
        usage_limit_message=(
            "I reached this run's limit before I could finish. I preserved the "
            "completed tool results and progress above so the work can continue."
        ),
        seen_tool_call_ids=seen_ids,
    )
    coordinator = coordinator_ref["coordinator"]

    await client.events(
        [
            {
                "type": "message_start",
                "user_message_id": str(context.user_message_id),
                "assistant_message_id": str(context.assistant_message_id),
            }
        ]
    )
    buffered_events: list[dict[str, Any]] = []
    last_flush = time.monotonic()

    async def flush_events(*, force: bool = False) -> None:
        nonlocal last_flush
        if not buffered_events:
            return
        if (
            not force
            and len(buffered_events) < 10
            and time.monotonic() - last_flush < 0.2
        ):
            return
        pending = list(buffered_events)
        buffered_events.clear()
        await client.events(pending)
        last_flush = time.monotonic()

    runner_task = asyncio.current_task()
    cancel_requested = asyncio.Event()

    async def monitor_cancellation() -> None:
        while True:
            await asyncio.sleep(1)
            if await client.cancelled():
                cancel_requested.set()
                if runner_task is not None:
                    runner_task.cancel()
                return

    cancel_monitor = asyncio.create_task(monitor_cancellation())
    try:
        async for chunk in coordinator.run():
            buffered_events.append(chunk.model_dump(mode="json", exclude_none=True))
            await flush_events()
        await flush_events(force=True)
    except asyncio.CancelledError:
        if cancel_requested.is_set():
            raise Cancelled("Builder job was cancelled") from None
        raise
    finally:
        cancel_monitor.cancel()
        try:
            await cancel_monitor
        except asyncio.CancelledError:
            pass

    result = coordinator.result()
    if broker is not None:
        digest = await broker.archive_to_callback(client)
    else:
        output_zip = scratch / "turn-output.zip"
        await asyncio.to_thread(zip_workspace, workspace_path, output_zip)
        digest = hashlib.sha256(output_zip.read_bytes()).hexdigest()
        await _put_file(client, "/output", output_zip)
    diagnostics = {
        **result.harness_diagnostics,
        "message_count": len(context.messages),
        "assistant_message_count": sum(
            message.role == "assistant" for message in context.messages
        ),
    }
    await client.complete(
        {
            "status": "succeeded",
            "output_sha256": digest,
            "final_text": result.final_text,
            "tool_call_count": result.tool_call_count,
            "model_request_count": result.model_request_count,
            "provider": provider_name_for_config(llm_config),
            "model": result.model,
            "token_count_input": result.token_count_input,
            "token_count_output": result.token_count_output,
            "cache_read_tokens": result.cache_read_tokens,
            "cache_write_tokens": result.cache_write_tokens,
            "provider_cost": (
                str(result.provider_cost) if result.provider_cost is not None else None
            ),
            "duration_ms": result.duration_ms,
            "sandbox_compute_ms": _sandbox_compute_ms(
                sandbox_compute_started,
                envelope,
            ),
            "assistant_message_id": str(context.assistant_message_id),
            "harness_diagnostics": diagnostics,
        }
    )


async def _stage_checkpoint(
    client: CallbackClient,
    workspace: Path,
    scratch: Path,
    *,
    broker_url: str | None = None,
) -> str:
    if broker_url is not None:
        async with WorkspaceBrokerClient(broker_url) as broker:
            return await broker.archive_to_callback(client)
    output = scratch / "checkpoint.zip"
    await asyncio.to_thread(zip_workspace, workspace, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    await _put_file(client, "/output", output)
    return digest


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            yield chunk


async def workspace_hydrate_command(args: argparse.Namespace) -> int:
    await hydrate_builder_turn_workspace(
        _file_chunks(Path(args.workspace_hydrate)),
        destination=Path(args.workspace),
        expected_sha256=args.expected_sha256,
        solution_id=args.solution_id,
    )
    return 0


async def workspace_tool_command(args: argparse.Namespace) -> int:
    request = json.loads(Path(args.workspace_tool).read_text())
    if not isinstance(request, dict):
        raise RunnerError("workspace tool request must be a JSON object")
    name = request.get("name")
    arguments = request.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise RunnerError("workspace tool request is invalid")
    if name == CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID:
        result = await _execute_workspace_command_tool(
            workspace=WorkspaceRoot(Path(args.workspace), WorkspaceLimits()),
            arguments=arguments,
        )
        Path(args.output).write_text(json.dumps(result))
        return 0
    result = await execute_builder_workspace_tool(
        workspace=WorkspaceRoot(Path(args.workspace), WorkspaceLimits()),
        bundle_path=request.get("bundle_path")
        if isinstance(request.get("bundle_path"), str)
        else None,
        name=name,
        arguments=arguments,
    )
    Path(args.output).write_text(json.dumps(result.runner_payload()))
    return 0


async def _execute_workspace_command_tool(
    *,
    workspace: WorkspaceRoot,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    argv = arguments.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > WORKSPACE_COMMAND_MAX_ARGS
        or not all(isinstance(arg, str) for arg in argv)
    ):
        return _workspace_command_error(
            "argv must be a non-empty list of strings within the argument limit"
        )
    for arg in argv:
        encoded = arg.encode("utf-8", errors="surrogatepass")
        if "\x00" in arg or len(encoded) > WORKSPACE_COMMAND_MAX_ARG_BYTES:
            return _workspace_command_error("argv contains an invalid argument")
    cwd_arg = arguments.get("cwd", ".")
    if cwd_arg in (None, "", "."):
        cwd = workspace.root
    elif isinstance(cwd_arg, str):
        try:
            cwd = workspace.resolve_target(cwd_arg)
            if not cwd.is_dir():
                return _workspace_command_error("cwd is not a directory")
        except Exception as exc:  # noqa: BLE001 - model-visible rejection
            return _workspace_command_error(str(exc))
    else:
        return _workspace_command_error("cwd must be a relative directory path")
    timeout = arguments.get("timeout_seconds", 30)
    if not isinstance(timeout, int):
        return _workspace_command_error("timeout_seconds must be an integer")
    timeout = max(1, min(WORKSPACE_COMMAND_MAX_TIMEOUT_SECONDS, timeout))
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/tmp",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "TMPDIR": "/tmp",
            },
            start_new_session=True,
        )
        stdout_task = asyncio.create_task(_read_limited_stream(process.stdout))
        stderr_task = asyncio.create_task(_read_limited_stream(process.stderr))
        stdout_result, stderr_result, _ = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, process.wait()),
            timeout=timeout,
        )
    except TimeoutError:
        await _terminate_process_group(process)
        return _workspace_command_error("command exceeded timeout", timed_out=True)
    except FileNotFoundError:
        return _workspace_command_error("command executable was not found")
    stdout_text, stdout_truncated = stdout_result
    stderr_text, stderr_truncated = stderr_result
    output_truncated = stdout_truncated or stderr_truncated
    return {
        "content": (
            f"Command exited {process.returncode}."
            + ("\n\nstdout:\n" + stdout_text if stdout_text else "")
            + ("\n\nstderr:\n" + stderr_text if stderr_text else "")
            + ("\n\n[output truncated]" if output_truncated else "")
        ),
        "structured_content": {
            "argv": argv,
            "cwd": "." if cwd == workspace.root else str(cwd.relative_to(workspace.root)),
            "exit_code": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "timed_out": False,
            "output_truncated": output_truncated,
        },
    }


async def _read_limited_stream(
    stream: asyncio.StreamReader | None,
) -> tuple[str, bool]:
    if stream is None:
        return "", False
    output = bytearray()
    total = 0
    truncated = False
    while chunk := await stream.read(8192):
        total += len(chunk)
        if len(output) < WORKSPACE_COMMAND_MAX_OUTPUT_BYTES:
            remaining = WORKSPACE_COMMAND_MAX_OUTPUT_BYTES - len(output)
            output.extend(chunk[:remaining])
        if total > WORKSPACE_COMMAND_MAX_OUTPUT_BYTES:
            truncated = True
    return output.decode("utf-8", errors="replace"), truncated


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
        return
    except TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


def _workspace_command_error(
    message: str,
    *,
    timed_out: bool = False,
) -> dict[str, Any]:
    return {
        "content": f"Error: {message}",
        "structured_content": {
            "error": message,
            "timed_out": timed_out,
        },
    }


async def workspace_archive_command(args: argparse.Namespace) -> int:
    output = Path(args.output)
    await asyncio.to_thread(zip_workspace, Path(args.workspace), output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    Path(args.digest_output).write_text(digest)
    return 0


async def run(envelope: Envelope, work_root: Path) -> int:
    scratch = Path(tempfile.mkdtemp(prefix="bifrost-job-", dir=work_root))
    os.chmod(scratch, 0o700)
    async with CallbackClient(envelope) as client:
        job_compute_started = time.monotonic()
        try:
            await client.progress("Starting Builder job")
            async with asyncio.timeout(envelope.timeout_seconds):
                if envelope.job_type == "solution.build":
                    await run_build(client, envelope, scratch)
                else:
                    await run_turn(client, envelope, scratch)
            return 0
        except (Cancelled, LocalBuildCancelled) as exc:
            body: dict[str, Any] = {"status": "cancelled", "error": str(exc)}
            if envelope.job_type == "solution.builder.turn":
                body["sandbox_compute_ms"] = _sandbox_compute_ms(
                    job_compute_started,
                    envelope,
                )
            workspace = scratch / "workspace"
            broker_url = envelope.broker_url()
            if (
                envelope.job_type == "solution.builder.turn"
                and (workspace.is_dir() or broker_url is not None)
            ):
                try:
                    body["checkpoint_output_sha256"] = await _stage_checkpoint(
                        client,
                        workspace,
                        scratch,
                        broker_url=broker_url,
                    )
                except Exception as checkpoint_error:  # noqa: BLE001
                    print(
                        f"Builder checkpoint could not be preserved: {checkpoint_error}",
                        file=sys.stderr,
                    )
            try:
                await client.complete(body)
            except RunnerError:
                return CALLBACK_FAILURE_EXIT
            return REPORTED_CANCELLED_EXIT
        except Exception as exc:  # noqa: BLE001 - terminal job boundary
            error_message = str(exc).strip() or (
                "Builder job exceeded its timeout"
                if isinstance(exc, TimeoutError)
                else f"Builder runner failed with {type(exc).__name__}"
            )
            traceback.print_exception(exc, file=sys.stderr)
            if envelope.job_type == "solution.build":
                body = {
                    "status": "timeout" if isinstance(exc, TimeoutError) else "failed",
                    "error": error_message[:4000],
                    "log_excerpt": getattr(exc, "log_excerpt", ""),
                }
            else:
                body = {"status": "failed", "error": error_message[:4000]}
                body["sandbox_compute_ms"] = _sandbox_compute_ms(
                    job_compute_started,
                    envelope,
                )
                workspace = scratch / "workspace"
                broker_url = envelope.broker_url()
                if workspace.is_dir() or broker_url is not None:
                    try:
                        body["checkpoint_output_sha256"] = await _stage_checkpoint(
                            client,
                            workspace,
                            scratch,
                            broker_url=broker_url,
                        )
                    except Exception as checkpoint_error:  # noqa: BLE001
                        print(
                            f"Builder checkpoint could not be preserved: {checkpoint_error}",
                            file=sys.stderr,
                        )
            try:
                await client.complete(body)
            except RunnerError:
                return CALLBACK_FAILURE_EXIT
            return REPORTED_FAILURE_EXIT
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def _read_envelope(args: argparse.Namespace) -> bytes:
    if args.envelope and args.envelope_file:
        raise RunnerError("provide an envelope path, --envelope, or stdin")
    if args.envelope_file:
        path = Path(args.envelope_file)
        if path.stat().st_size > MAX_ENVELOPE_BYTES:
            raise RunnerError("envelope exceeds size limit")
        return path.read_bytes()
    if args.envelope:
        return args.envelope.encode()
    return sys.stdin.buffer.read(MAX_ENVELOPE_BYTES + 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope_file", nargs="?")
    parser.add_argument("--envelope")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--workspace-hydrate")
    parser.add_argument("--workspace-tool")
    parser.add_argument("--workspace-archive")
    parser.add_argument("--workspace")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--solution-id")
    parser.add_argument("--output")
    parser.add_argument("--digest-output")
    args = parser.parse_args(argv)
    if args.probe:
        print(
            json.dumps(
                {
                    "ready": True,
                    "schema_version": SCHEMA_VERSION,
                    "harness": "pydantic-ai",
                }
            )
        )
        return 0
    try:
        if args.workspace_hydrate:
            if not args.workspace or not args.expected_sha256 or not args.solution_id:
                raise RunnerError(
                    "--workspace-hydrate requires --workspace, --expected-sha256, "
                    "and --solution-id"
                )
            return asyncio.run(workspace_hydrate_command(args))
        if args.workspace_tool:
            if not args.workspace or not args.output:
                raise RunnerError("--workspace-tool requires --workspace and --output")
            return asyncio.run(workspace_tool_command(args))
        if args.workspace_archive:
            if not args.output or not args.digest_output:
                raise RunnerError(
                    "--workspace-archive requires --output and --digest-output"
                )
            args.workspace = args.workspace_archive
            return asyncio.run(workspace_archive_command(args))
        raw = _read_envelope(args)
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise RunnerError("envelope exceeds size limit")
        envelope = Envelope.model_validate_json(raw)
        root = Path(os.getenv("BIFROST_RUNNER_WORKDIR", "/work"))
        root.mkdir(parents=True, exist_ok=True)
        return asyncio.run(run(envelope, root))
    except Exception as exc:  # noqa: BLE001 - command boundary
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
