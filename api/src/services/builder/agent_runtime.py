"""The builder's coding-agent tool loop, driven by Bifrost's own LLM client.

The 2026-07-25 builder spec ("Agent framework") rejected the OpenAI Agents SDK
for the first release: the builder needs one agent, a handful of typed local
tools, no handoffs, and no multi-agent graph, while the SDK's value is
concentrated in features unused here. So the loop lives here, on top of
:class:`~src.services.llm.base.BaseLLMClient`, which already normalizes tool
calls across providers.

:class:`BuilderAgentRuntime` is the swap seam named in the spec — if the builder
ever outgrows a single-agent loop, a different implementation goes behind this
interface and the turn service is unchanged.

Every tool the model can call is a method on a :class:`WorkspaceRoot`, so the
security rules live in ``fs_tools`` and this module only translates. A refused
operation (:class:`WorkspaceViolation`) comes back to the model as an ordinary
tool-error string: the model is expected to notice and adjust, and a hostile or
confused model must not be able to abort the turn by asking for a bad path.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from src.services.builder.fs_tools import WorkspaceRoot, WorkspaceViolation
from src.services.llm.base import (
    BaseLLMClient,
    LLMMessage,
    ToolCallRequest,
    ToolDefinition,
)

# Ceiling on the string handed back for a single tool call. fs_tools already
# bounds what it reads off disk; this bounds what assembling many of those
# bounded pieces (a wide search, a long listing) can add to the context.
MAX_TOOL_RESULT_CHARS = 64 * 1024

StoppedReason = Literal["done", "max_iterations", "error"]

BUILDER_SYSTEM_PROMPT = """\
You are the Bifrost Solution builder. You edit one Solution workspace on behalf \
of its owner, and the tools provided are your only way to see or change it. \
There is no shell, no network, and no access to anything outside the workspace.

Workspace layout:
- `bifrost.solution.yaml` is the Solution descriptor at the workspace root. It \
carries `slug` and `name` and must remain valid YAML with those keys present.
- `.bifrost/*.yaml` holds per-entity manifests (workflows, apps, tables, forms).
- `workflows/` holds Python workflow modules.
- `apps/<app>/src/` holds app source.

How to work:
- Read before you write. Use `list_files` and `search_text` to locate things, \
and `read_file` to see current contents; never guess at a file you have not read.
- Prefer `apply_patch` for edits to existing files: it replaces one exact \
string with another, so the change stays small and reviewable. `old_string` \
must appear exactly once unless you pass `replace_all`. Use `write_file` for \
new files or a genuine full rewrite.
- Call `validate_solution` before you finish and fix anything it reports.
- Never write secrets, API keys, tokens, or passwords into workspace files. \
Configuration values belong in Solution config, not in source.
- When you are done, stop calling tools and reply with a short plain-language \
summary of what you changed.
"""


@dataclass(frozen=True)
class ToolCallRecord:
    """One executed tool call, kept so the caller can show its own work.

    ``result`` is the same string the model saw, truncated to the same ceiling,
    so a chatty tool cannot bloat whatever row this ends up in.
    """

    name: str
    arguments: dict[str, Any]
    result: str
    ok: bool
    duration_ms: int | None = None


@dataclass
class TurnResult:
    """Everything one builder turn produced, for the caller to persist."""

    final_text: str
    tool_calls: list[ToolCallRecord]
    input_tokens: int
    output_tokens: int
    iterations: int
    stopped_reason: StoppedReason

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    )


_PATH_PROP = {"type": "string", "description": "Path relative to the workspace root."}


BUILDER_TOOLS: list[ToolDefinition] = [
    _tool(
        "list_files",
        "List every file in the workspace as relative paths.",
        {},
        [],
    ),
    _tool(
        "read_file",
        "Read a workspace file as UTF-8 text. Long files are truncated.",
        {"path": _PATH_PROP},
        ["path"],
    ),
    _tool(
        "search_text",
        "Regex-search workspace files and return matching lines with line numbers.",
        {
            "pattern": {"type": "string", "description": "Python regular expression."},
            "glob": {
                "type": "string",
                "description": "Optional glob limiting which files are searched, e.g. 'workflows/*.py'.",
            },
        },
        ["pattern"],
    ),
    _tool(
        "write_file",
        "Create a file or overwrite it entirely with the given content.",
        {"path": _PATH_PROP, "content": {"type": "string", "description": "Full new file content."}},
        ["path", "content"],
    ),
    _tool(
        "apply_patch",
        (
            "Replace an exact string in an existing file. old_string must match "
            "exactly once unless replace_all is true."
        ),
        {
            "path": _PATH_PROP,
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring a unique match.",
            },
        },
        ["path", "old_string", "new_string"],
    ),
    _tool("delete_file", "Delete a workspace file.", {"path": _PATH_PROP}, ["path"]),
    _tool(
        "make_directory",
        "Create a directory, including any missing parents.",
        {"path": _PATH_PROP},
        ["path"],
    ),
    _tool(
        "validate_solution",
        "Check that the workspace is a valid Solution: descriptor present and well-formed.",
        {},
        [],
    ),
]


class BuilderAgentRuntime(ABC):
    """Runs one builder turn against a workspace. The spec's swap seam."""

    @abstractmethod
    async def run_turn(
        self,
        instructions: str,
        user_message: str,
        workspace: WorkspaceRoot,
        *,
        history: list[dict[str, Any]] | None = None,
        max_iterations: int = 40,
    ) -> TurnResult:
        """Drive the model until it stops calling tools or hits the iteration cap.

        ``instructions`` is the system prompt, ``history`` is prior turns as
        ``{"role", "content"}`` dicts, and every workspace mutation happens
        through ``workspace``.
        """
        ...


class InternalLoopRuntime(BuilderAgentRuntime):
    """The default runtime: a plain tool loop over :class:`BaseLLMClient`."""

    def __init__(self, client: BaseLLMClient) -> None:
        self._client = client

    async def run_turn(
        self,
        instructions: str,
        user_message: str,
        workspace: WorkspaceRoot,
        *,
        history: list[dict[str, Any]] | None = None,
        max_iterations: int = 40,
    ) -> TurnResult:
        messages: list[LLMMessage] = [LLMMessage(role="system", content=instructions)]
        for entry in history or []:
            messages.append(
                LLMMessage(role=entry["role"], content=entry.get("content"))
            )
        messages.append(LLMMessage(role="user", content=user_message))

        final_text = ""
        tool_calls: list[ToolCallRecord] = []
        input_tokens = 0
        output_tokens = 0
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            response = await self._client.complete(messages, tools=BUILDER_TOOLS)
            input_tokens += response.input_tokens or 0
            output_tokens += response.output_tokens or 0

            if not response.tool_calls:
                final_text = response.content or ""
                return TurnResult(
                    final_text=final_text,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    iterations=iterations,
                    stopped_reason="done",
                )

            messages.append(
                LLMMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            # The model's narration alongside a tool call is the best summary
            # available if the loop later runs out of iterations.
            if response.content:
                final_text = response.content

            for call in response.tool_calls:
                started = time.monotonic()
                result, ok = _execute_tool(call, workspace)
                duration_ms = int((time.monotonic() - started) * 1000)
                bounded = result[:MAX_TOOL_RESULT_CHARS]
                tool_calls.append(
                    ToolCallRecord(
                        name=call.name,
                        arguments=call.arguments,
                        result=bounded,
                        ok=ok,
                        duration_ms=duration_ms,
                    )
                )
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=bounded,
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )

        return TurnResult(
            final_text=final_text,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            iterations=iterations,
            stopped_reason="max_iterations",
        )


def _execute_tool(call: ToolCallRequest, workspace: WorkspaceRoot) -> tuple[str, bool]:
    """Run one tool call, returning the string the model sees and whether it worked.

    Refusals and bad arguments are values, not exceptions: the model gets an
    ``Error:`` line and another chance rather than the turn dying. The boolean
    is what the caller records, so a failed call stays distinguishable from a
    successful one whose output merely starts with the word "Error".
    """
    try:
        return _dispatch(call.name, call.arguments, workspace), True
    except WorkspaceViolation as exc:
        return f"Error: {exc}", False
    except (KeyError, TypeError, ValueError) as exc:
        # Malformed tool arguments — a missing key, a number where a string
        # belongs. Telling the model what was wrong lets it retry.
        return f"Error: invalid arguments for {call.name}: {exc}", False


def _dispatch(name: str, args: dict[str, Any], workspace: WorkspaceRoot) -> str:
    if name == "list_files":
        paths = workspace.list_files()
        return "\n".join(paths) if paths else "(workspace is empty)"

    if name == "read_file":
        data, truncated = workspace.read_file(_require_str(args, "path"))
        text = data.decode("utf-8", errors="replace")
        return f"{text}\n\n[truncated at read limit]" if truncated else text

    if name == "search_text":
        hits = workspace.search_text(
            _require_str(args, "pattern"),
            rel_glob=args.get("glob") or "**/*",
        )
        if not hits:
            return "No matches."
        return "\n".join(f"{h.path}:{h.line_number}: {h.line}" for h in hits)

    if name == "write_file":
        path = _require_str(args, "path")
        workspace.write_file(path, _require_str(args, "content").encode("utf-8"))
        return f"Wrote {path}."

    if name == "apply_patch":
        return _apply_patch(
            workspace,
            _require_str(args, "path"),
            _require_str(args, "old_string"),
            _require_str(args, "new_string"),
            bool(args.get("replace_all", False)),
        )

    if name == "delete_file":
        path = _require_str(args, "path")
        workspace.delete_file(path)
        return f"Deleted {path}."

    if name == "make_directory":
        path = _require_str(args, "path")
        workspace.make_directory(path)
        return f"Created directory {path}."

    if name == "validate_solution":
        return _validate_solution(workspace)

    return f"Error: unknown tool '{name}'."


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string")
    return value


def _apply_patch(
    workspace: WorkspaceRoot,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> str:
    """Exact-string replacement built on the read/write primitives.

    A truncated read would make an edit silently drop the tail of the file, so
    a file too large to read whole is refused outright.
    """
    if not old_string:
        raise WorkspaceViolation("old_string must not be empty")

    data, truncated = workspace.read_file(path)
    if truncated:
        raise WorkspaceViolation("file is too large to patch; rewrite it instead")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceViolation("file is not valid UTF-8 text") from exc

    occurrences = text.count(old_string)
    if occurrences == 0:
        raise WorkspaceViolation("old_string not found in file")
    if occurrences > 1 and not replace_all:
        raise WorkspaceViolation(
            f"old_string matches {occurrences} times; make it unique or pass replace_all"
        )

    count = -1 if replace_all else 1
    workspace.write_file(path, text.replace(old_string, new_string, count).encode("utf-8"))
    replaced = occurrences if replace_all else 1
    return f"Patched {path} ({replaced} replacement{'s' if replaced != 1 else ''})."


def _validate_solution(workspace: WorkspaceRoot) -> str:
    """Check the workspace against the canonical Solution descriptor schema."""
    # Imported here for the same reason the solutions services do it: the CLI
    # package is a sibling distribution, and the import stays local to the one
    # call site that needs it.
    from bifrost.solution_descriptor import DESCRIPTOR_FILENAME, load_descriptor

    try:
        descriptor = load_descriptor(workspace.root)
    except FileNotFoundError:
        return f"Invalid: workspace has no {DESCRIPTOR_FILENAME} at its root."
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return f"Invalid: {DESCRIPTOR_FILENAME} failed validation: {problems}"
    except (OSError, yaml.YAMLError) as exc:
        return f"Invalid: {DESCRIPTOR_FILENAME} could not be parsed: {exc}"

    return (
        f"Valid: Solution '{descriptor.slug}' ({descriptor.name}), "
        f"{len(workspace.list_files())} files."
    )
