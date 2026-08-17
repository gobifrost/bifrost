#!/usr/bin/env python3
"""Cloudflare-hosted executor for Bifrost's shared Pydantic AI runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.usage import RunUsage
from shared.sandbox_runner_protocol import (
    SandboxBuilderToolResponse,
    SandboxBuilderTurnContext,
)

from src.services.agent_runtime import (
    AgentRunBudget,
    AgentRuntimeRunner,
    ModelCallEvent,
    agent_model_settings,
    create_agent_model,
    provider_reported_cost,
)
from src.services.agent_runtime.history import build_runtime_message_history
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.builder.local_app_build import (
    LocalBuildCancelled,
    materialize_build_input,
    run_local_app_build,
)
from src.services.builder.scaffold import zip_workspace
from src.services.builder.workspace_tool_runtime import (
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


class RunnerError(RuntimeError):
    pass


class Cancelled(RunnerError):
    pass


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
    storage = CallbackBuildStorage(client)
    workspace_path = scratch / "workspace"
    workspace_path.mkdir(mode=0o700)
    await materialize_build_input(
        storage,  # type: ignore[arg-type]
        workspace_path,
        expected_sha256=envelope.input_sha256,
    )
    workspace = WorkspaceRoot(workspace_path, WorkspaceLimits())
    context = SandboxBuilderTurnContext.model_validate(
        await client.json("GET", "/context")
    )
    history = await _runtime_history(client, context)
    history_messages = history[1:]
    if not history_messages or history_messages[-1].role != "user":
        raise RunnerError("Builder conversation has no current user prompt")
    current_prompt: Any = PydanticAIClient.convert_user_content(
        history_messages.pop()
    )

    llm_config = LLMConfig(
        provider=context.llm_config.provider,
        model=context.llm_config.model,
        api_key=context.llm_config.api_key,
        endpoint=context.llm_config.endpoint,
        max_tokens=context.llm_config.max_tokens,
        extra_params=context.llm_config.extra_params,
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
    totals = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
    }
    total_provider_cost = Decimal("0")
    provider_cost_seen = False
    model_name = context.llm_config.model
    tool_counts: Counter[str] = Counter()
    tool_error_counts: Counter[str] = Counter()
    ready: dict[str, asyncio.Event] = {}
    display_ids: dict[str, str] = {}
    seen_ids = {
        call.id
        for message in history
        if message.role == "assistant"
        for call in message.tool_calls or []
    }

    async def model_event(event: ModelCallEvent) -> None:
        nonlocal total_provider_cost, provider_cost_seen, model_name
        if event.type == "request":
            await client.progress("AI is working")
        elif event.type == "error":
            await client.progress("AI request failed")
        if event.type != "response" or event.response is None:
            return
        response_usage = event.response.usage
        totals["input"] += response_usage.input_tokens
        totals["output"] += response_usage.output_tokens
        totals["cache_read"] += response_usage.cache_read_tokens
        totals["cache_write"] += response_usage.cache_write_tokens
        cost = provider_reported_cost(event.response)
        if cost is not None:
            total_provider_cost += cost
            provider_cost_seen = True
        if event.response.model_name:
            model_name = event.response.model_name

    async def execute_tool(
        name: str,
        arguments: dict[str, Any],
        internal_call_id: str,
    ) -> str:
        event = ready.setdefault(internal_call_id, asyncio.Event())
        await event.wait()
        display_id = display_ids[internal_call_id]
        tool_counts[name] += 1
        await client.progress(f"Using {name}")
        started = await client.json(
            "POST",
            "/tools/start",
            body={
                "tool_call_id": display_id,
                "name": name,
                "arguments": arguments,
            },
            timeout=envelope.timeout_seconds,
        )
        response = SandboxBuilderToolResponse.model_validate(started)
        if response.model_content is not None:
            if response.error:
                tool_error_counts[name] += 1
            return response.model_content
        if response.execution != "sandbox":
            if response.error:
                tool_error_counts[name] += 1
                return f"Error: {response.error}"
            raise RunnerError("Bifrost tool callback returned no model content")

        began = time.monotonic()
        try:
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
            tool_error_counts[name] += 1
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
        return completion.model_content

    buffered_events: list[dict[str, Any]] = []
    last_flush = time.monotonic()
    compaction_count = 0

    async def record_compaction(
        before_tokens: int,
        after_tokens: int,
    ) -> None:
        nonlocal compaction_count
        compaction_count += 1
        buffered_events.append(
            {
                "type": "context_warning",
                "context_warning": {
                    "current_tokens": after_tokens,
                    "max_tokens": budget.context_target_tokens,
                    "action": "compacted",
                    "message": (
                        "Compacted the active context from about "
                        f"{before_tokens:,} to {after_tokens:,} tokens."
                    ),
                },
            }
        )

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
        tool_executor=execute_tool,
        model_event_handler=model_event,
        compaction_event_handler=record_compaction,
        toolset_id=f"bifrost-builder-{context.solution_id}",
    )

    await client.events(
        [
            {
                "type": "message_start",
                "user_message_id": str(context.user_message_id),
                "assistant_message_id": str(context.assistant_message_id),
            }
        ]
    )
    async def flush_events(*, force: bool = False) -> None:
        nonlocal last_flush
        if not buffered_events:
            return
        if not force and len(buffered_events) < 10 and time.monotonic() - last_flush < 0.2:
            return
        pending = list(buffered_events)
        buffered_events.clear()
        await client.events(pending)
        last_flush = time.monotonic()

    started_at = time.monotonic()
    final_text = ""
    current_response = ""
    current_segment_persisted = False
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
        async with runtime.run_stream_events(
            current_prompt,
            message_history=PydanticAIClient.convert_messages(history_messages),
            usage_limits=budget.usage_limits(),
            usage=usage,
            conversation_id=context.conversation_id,
        ) as events:
            async for event in events:
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    current_response += event.part.content
                    if event.part.content:
                        buffered_events.append(
                            {"type": "delta", "content": event.part.content}
                        )
                        await flush_events()
                elif isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta,
                    TextPartDelta,
                ):
                    current_response += event.delta.content_delta
                    if event.delta.content_delta:
                        buffered_events.append(
                            {"type": "delta", "content": event.delta.content_delta}
                        )
                        await flush_events()
                elif isinstance(event, FunctionToolCallEvent):
                    await flush_events(force=True)
                    if current_response and not current_segment_persisted:
                        await client.json(
                            "POST",
                            "/assistant-segments",
                            body={"content": current_response},
                        )
                        current_segment_persisted = True
                    call_id = event.part.tool_call_id
                    display_id = call_id
                    if display_id in seen_ids:
                        display_id = f"{display_id}_run{usage.requests}"
                    seen_ids.add(display_id)
                    display_ids[call_id] = display_id
                    ready.setdefault(call_id, asyncio.Event()).set()
                    current_response = ""
                    current_segment_persisted = False
                elif isinstance(event, AgentRunResultEvent):
                    final_text = str(event.result.output or "")
            await flush_events(force=True)
    except UsageLimitExceeded:
        final_text = current_response or (
            "I reached this run's limit before I could finish. I preserved the "
            "completed tool results and progress above so the work can continue."
        )
        buffered_events.append(
            {
                "type": "context_warning",
                "context_warning": {
                    "current_tokens": usage.total_tokens,
                    "max_tokens": context.max_token_budget,
                    "action": "warning",
                    "message": "The agent reached its run budget and left a resumable handoff.",
                },
            }
        )
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

    output_zip = scratch / "turn-output.zip"
    await asyncio.to_thread(zip_workspace, workspace_path, output_zip)
    digest = hashlib.sha256(output_zip.read_bytes()).hexdigest()
    await _put_file(client, "/output", output_zip)
    diagnostics = {
        "message_count": len(context.messages),
        "assistant_message_count": sum(
            message.role == "assistant" for message in context.messages
        ),
        "tool_call_count": sum(tool_counts.values()),
        "tool_error_count": sum(tool_error_counts.values()),
        "compaction_count": compaction_count,
        "retry_count": 0,
        "truncated": False,
        "tools": [
            {
                "name": name,
                "count": count,
                "error_count": tool_error_counts[name],
            }
            for name, count in tool_counts.most_common(32)
        ],
        "other_tool_call_count": sum(
            count for _name, count in tool_counts.most_common()[32:]
        ),
    }
    await client.complete(
        {
            "status": "succeeded",
            "output_sha256": digest,
            "final_text": final_text,
            "tool_call_count": sum(tool_counts.values()),
            "model_request_count": usage.requests,
            "provider": provider_name_for_config(llm_config),
            "model": model_name,
            "token_count_input": totals["input"],
            "token_count_output": totals["output"],
            "cache_read_tokens": totals["cache_read"],
            "cache_write_tokens": totals["cache_write"],
            "provider_cost": (
                str(total_provider_cost) if provider_cost_seen else None
            ),
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "assistant_message_id": str(context.assistant_message_id),
            "harness_diagnostics": diagnostics,
        }
    )


async def _stage_checkpoint(
    client: CallbackClient,
    workspace: Path,
    scratch: Path,
) -> str:
    output = scratch / "checkpoint.zip"
    await asyncio.to_thread(zip_workspace, workspace, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    await _put_file(client, "/output", output)
    return digest


async def run(envelope: Envelope, work_root: Path) -> int:
    scratch = Path(tempfile.mkdtemp(prefix="bifrost-job-", dir=work_root))
    os.chmod(scratch, 0o700)
    async with CallbackClient(envelope) as client:
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
            workspace = scratch / "workspace"
            if envelope.job_type == "solution.builder.turn" and workspace.is_dir():
                try:
                    body["checkpoint_output_sha256"] = await _stage_checkpoint(
                        client,
                        workspace,
                        scratch,
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
            print(error_message, file=sys.stderr)
            if envelope.job_type == "solution.build":
                body = {
                    "status": "timeout" if isinstance(exc, TimeoutError) else "failed",
                    "error": error_message[:4000],
                    "log_excerpt": getattr(exc, "log_excerpt", ""),
                }
            else:
                body = {"status": "failed", "error": error_message[:4000]}
                workspace = scratch / "workspace"
                if workspace.is_dir():
                    try:
                        body["checkpoint_output_sha256"] = await _stage_checkpoint(
                            client,
                            workspace,
                            scratch,
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
