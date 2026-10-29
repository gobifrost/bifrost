"""
Unified Execution Engine
Single source of truth for all code execution (workflows, scripts, data providers)
"""

import asyncio
import copy
import inspect
import json
import logging
import os
import re
import signal
import sys
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import CodeType
from typing import Any, Callable, Iterator, Mapping, get_type_hints, get_origin, get_args, Union

from pydantic import BaseModel

from bifrost._runtime_io import write_log

from src.sdk.context import Caller, ExecutionContext, Organization
from src.sdk.error_handling import WorkflowError
from src.sdk.errors import UserError, WorkflowExecutionException
from src.models.enums import ExecutionStatus
from src.core.error_messages import format_exception_message
from src.core.secret_string import redact_secrets

logger = logging.getLogger(__name__)


_RUNTIME_TRANSPORT_LOGGERS = (
    "httpcore",
    "hpack",
    "h2",
    "h11",
    "urllib3.connectionpool",
)


def _is_runtime_transport_logger(name: str) -> bool:
    return any(
        name == family or name.startswith(f"{family}.")
        for family in _RUNTIME_TRANSPORT_LOGGERS
    )


_RUNTIME_CREDENTIAL_HEADERS = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
)
_RUNTIME_CREDENTIAL_HEADER_PATTERN = "|".join(_RUNTIME_CREDENTIAL_HEADERS)
_RUNTIME_HEADER_LINE_RE = re.compile(
    rf"(?i)(\b(?:{_RUNTIME_CREDENTIAL_HEADER_PATTERN})\s*:\s*)(.*?)(?=(?:[\r\n]|\\[rn])|$)"
)
_RUNTIME_HEADER_STRUCTURED_RE = re.compile(
    rf"(?i)((?:b)?(?:\\?['\"])(?:{_RUNTIME_CREDENTIAL_HEADER_PATTERN})(?:\\?['\"])\s*[:,]\s*(?:b)?)"
)


def _quoted_log_scalar_end(message: str, start: int) -> int | None:
    """Return the end of a quoted repr/JSON scalar without splitting its value."""
    escaped_delimiter = message.startswith("\\'", start) or message.startswith('\\"', start)
    delimiter_length = 2 if escaped_delimiter else 1
    quote = message[start + delimiter_length - 1]
    index = start + delimiter_length
    while index < len(message):
        if escaped_delimiter:
            if message.startswith(f"\\{quote}", index):
                following = index + 2
                while following < len(message) and message[following].isspace():
                    following += 1
                if following == len(message) or message[following] in ",)]}":
                    return index + 2
                index += 2
                continue
        elif message[index] == "\\":
            index += 2
            continue
        elif message[index] == quote:
            return index + 1
        index += 1
    return None


def _sanitize_structured_header_values(message: str) -> str:
    """Redact quoted dict and bytes-tuple header values without truncating them."""
    sanitized: list[str] = []
    cursor = 0
    for match in _RUNTIME_HEADER_STRUCTURED_RE.finditer(message):
        if match.start() < cursor:
            continue
        value_start = match.end()
        if value_start >= len(message) or message[value_start] not in "'\\\"":
            continue
        value_end = _quoted_log_scalar_end(message, value_start)
        if value_end is None:
            delimiter_length = 2 if message[value_start] == "\\" else 1
            delimiter = message[value_start:value_start + delimiter_length]
            sanitized.append(message[cursor:value_start])
            sanitized.append(f"{delimiter}[REDACTED]")
            return "".join(sanitized)
        delimiter_length = 2 if message[value_start] == "\\" else 1
        delimiter = message[value_start:value_start + delimiter_length]
        sanitized.append(message[cursor:value_start])
        sanitized.append(f"{delimiter}[REDACTED]{delimiter}")
        cursor = value_end
    sanitized.append(message[cursor:])
    return "".join(sanitized)


def _sanitize_runtime_log_message(message: str, secret_values: set[str]) -> str:
    """Redact credentials from HTTP header renderings before they leave the engine."""
    sanitized = _sanitize_structured_header_values(message)
    sanitized = _RUNTIME_HEADER_LINE_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED]", sanitized
    )
    return redact_secrets(sanitized, secret_values)


class _RuntimeLogSanitizingFormatter(logging.Formatter):
    """Format a cloned record so console output cannot alter capture behavior."""

    def __init__(
        self,
        formatter: logging.Formatter | None,
        secret_values: Callable[[], set[str]],
    ) -> None:
        super().__init__()
        self._formatter = formatter or logging.Formatter()
        self._secret_values = secret_values

    def format(self, record: logging.LogRecord) -> str:
        formatted = self._formatter.format(copy.copy(record))
        return _sanitize_runtime_log_message(formatted, self._secret_values())


@contextmanager
def _runtime_logging_scope(
    root_logger: logging.Logger,
    secret_values: Callable[[], set[str]],
) -> Iterator[None]:
    """Capture user DEBUG logs while keeping transport noise and credentials scoped."""
    root_level = root_logger.level
    handlers = list(root_logger.handlers)
    handler_formatters = [(handler, handler.formatter) for handler in handlers]
    logger_levels: dict[logging.Logger, int] = {}
    for name, candidate in logging.root.manager.loggerDict.items():
        if not isinstance(candidate, logging.Logger):
            continue
        if _is_runtime_transport_logger(name):
            logger_levels[candidate] = candidate.level
    for family in _RUNTIME_TRANSPORT_LOGGERS:
        transport_logger = logging.getLogger(family)
        logger_levels.setdefault(transport_logger, transport_logger.level)

    for handler, formatter in handler_formatters:
        handler.setFormatter(_RuntimeLogSanitizingFormatter(formatter, secret_values))
    for transport_logger, level in logger_levels.items():
        transport_logger.setLevel(max(level, logging.INFO))
    root_logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root_logger.setLevel(root_level)
        for transport_logger, level in logger_levels.items():
            transport_logger.setLevel(level)
        for handler, formatter in handler_formatters:
            handler.setFormatter(formatter)


def _human_size(num_bytes: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f}{unit}" if unit != "B" else f"{num_bytes}{unit}"
        num_bytes /= 1024  # type: ignore[assignment]
    return f"{num_bytes:.1f}TB"


# Attribute naming the code object whose locals are captured when it returns,
# for callables that run user code in an inner function (inline scripts).
_CAPTURE_CODE_ATTR = "_bifrost_capture_code"

# sys.monitoring tool ids CPython leaves unassigned (0, 1, 2 and 5 are the
# conventional debugger, coverage, profiler and optimizer ids).
_CAPTURE_TOOL_IDS = (3, 4)


@contextmanager
def _capture_locals_on_return(
    code: CodeType, capture: Callable[[Mapping[str, Any]], None]
) -> Iterator[None]:
    """Call ``capture`` with the frame's locals whenever ``code`` returns.

    A PEP 669 ``PY_RETURN`` event scoped to ``code`` alone fires once per
    return; no other code is instrumented and no per-line or per-call Python
    callback runs. An await suspension raises ``PY_YIELD`` and an exception
    raises ``PY_UNWIND``, so neither reaches ``capture``. ``sys.settrace`` is
    left alone, so an attached debugger keeps its tracer.
    """
    monitoring = sys.monitoring
    tool_id = next(
        (t for t in _CAPTURE_TOOL_IDS if monitoring.get_tool(t) is None), None
    )
    if tool_id is None:
        raise RuntimeError(
            "Workflow variable capture could not get a sys.monitoring tool id: "
            f"ids {_CAPTURE_TOOL_IDS} are all in use"
        )

    def on_return(returning_code: CodeType, instruction_offset: int, retval: object) -> None:
        # The returning frame is the caller of this callback.
        capture(sys._getframe(1).f_locals)

    monitoring.use_tool_id(tool_id, "bifrost-variable-capture")
    try:
        monitoring.register_callback(tool_id, monitoring.events.PY_RETURN, on_return)
        monitoring.set_local_events(tool_id, code, monitoring.events.PY_RETURN)
        yield
    finally:
        monitoring.set_local_events(tool_id, code, monitoring.events.NO_EVENTS)
        monitoring.register_callback(tool_id, monitoring.events.PY_RETURN, None)
        monitoring.free_tool_id(tool_id)


# Import bifrost context management for SDK support
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from bifrost._context import set_execution_context, clear_execution_context
    BIFROST_CONTEXT_AVAILABLE = True
except ImportError:
    logger.warning(
        "Bifrost SDK not available - user workflows cannot use bifrost SDK")
    BIFROST_CONTEXT_AVAILABLE = False

@dataclass
class ServiceRunConfig:
    """Supervision config for one service attempt (engine service mode)."""

    service_id: str
    attempt_id: str
    lease_token: str
    revision: str | None = None
    graceful_shutdown_seconds: int = 30
    startup_grace_seconds: int = 60
    # Initial renewable credential; the supervisor installs fresher ones
    # from the parent's Redis handoff as they arrive.
    token: str = ""
    token_expires_at: str = ""


@dataclass
class ExecutionRequest:
    """Request to execute code or a function"""
    execution_id: str
    caller: Caller
    organization: Organization | None
    # Function to execute (preferred - passed directly from discovery)
    func: Any = None                     # The actual callable function

    # Alternative: inline code (mutually exclusive with func)
    code: str | None = None              # Base64 Python code

    # Function name (for display/logging purposes)
    name: str | None = None              # Function name (from metadata)

    # Tags for execution type (e.g., ["workflow"], ["data_provider"])
    tags: list[str] = field(default_factory=list)

    # Execution settings
    timeout_seconds: int = 1800          # Default 30 minutes
    workflow_deadline: datetime | None = None
    cache_ttl_seconds: int = 300         # For data providers

    # Parameters
    parameters: dict[str, Any] = field(default_factory=dict)

    # Launch workflow results (available via context.startup)
    startup: Any | None = None
    form_inputs: dict[str, Any] = field(default_factory=dict)
    embed: dict[str, Any] = field(default_factory=dict)

    # ROI initialization (from workflow defaults)
    roi: dict[str, Any] | None = None

    # Flags
    transient: bool = False              # Don't write to DB
    no_cache: bool = False               # For data providers
    is_platform_admin: bool = False       # For platform admin executions

    # Real-time updates
    broadcaster: Any = None              # WebPubSubBroadcaster for streaming logs

    # Event context (set when triggered by an event subscription)
    event: Any = None                    # EventContext | None

    # The install this execution belongs to, when the workflow is solution-managed
    # (from the resolved workflow row's solution_id). Carried onto ExecutionContext
    # so the SDK can scope name lookups (tables/configs) to the install's OWN
    # entity — own-first, then _repo/. None for plain _repo/ executions.
    solution_id: str | None = None
    artifact_workspace_id: str | None = None

    # Service mode (Slice 3): set for supervised @service runs. The engine
    # invokes the service coroutine with cooperative stop (no timeout kill,
    # no variable tracing) and attempt-scoped log streaming.
    service: ServiceRunConfig | None = None

    # The pre-minted engine token this process installed as its SDK
    # credentials (see worker.py::_set_process_engine_credentials).
    # Registered as a dynamic secret so _scrub_outputs redacts it from
    # results, logs, variables and error messages.
    engine_token: str | None = None


@dataclass
class ExecutionResult:
    """Result of code/function execution"""
    execution_id: str
    status: ExecutionStatus
    result: Any
    duration_ms: int

    # Captured data
    logs: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] | None = None  # Only for inline scripts
    integration_calls: list[dict[str, Any]] = field(default_factory=list)

    # ROI (final values after execution)
    roi: dict[str, Any] | None = None

    # Persisted execution context snapshot
    execution_context: dict[str, Any] | None = None

    # Error details
    error_message: str | None = None
    error_type: str | None = None

    # Data provider specific
    cached: bool = False
    cache_expires_at: str | None = None


def _coerce_params_to_type_hints(func: Any, params: dict[str, Any]) -> dict[str, Any]:
    """
    Coerce parameter values to match function type hints.

    GET query parameters are always strings, but workflow functions may expect
    int, float, bool, etc. This function inspects the function signature and
    converts parameters accordingly.

    Args:
        func: The workflow function to get type hints from
        params: Dictionary of parameters (may be strings from query params)

    Returns:
        Dictionary with parameters coerced to the correct types
    """
    if not params:
        return {}

    result: dict[str, Any] = {}

    # Get type hints from the workflow function
    try:
        hints = get_type_hints(func)
    except Exception:
        # If we can't get type hints, return params as-is
        return dict(params)

    for key, value in params.items():
        if key not in hints:
            # No type hint, keep as-is
            result[key] = value
            continue

        expected_type = hints[key]

        # Handle Optional types (Union[X, None])
        origin = get_origin(expected_type)
        if origin is Union:
            # Filter out NoneType from Union args
            args = get_args(expected_type)
            non_none_types = [t for t in args if t is not type(None)]
            if non_none_types:
                expected_type = non_none_types[0]

        # Only coerce string values
        if not isinstance(value, str):
            result[key] = value
            continue

        # Coerce based on expected type
        try:
            if expected_type is int:
                result[key] = int(value)
            elif expected_type is float:
                result[key] = float(value)
            elif expected_type is bool:
                result[key] = value.lower() in ("true", "1", "yes", "on")
            else:
                result[key] = value
        except (ValueError, TypeError):
            # Coercion failed, keep as string (will fail validation later)
            result[key] = value

    return result


def _scrub_outputs(
    context: ExecutionContext,
    result: Any = None,
    variables: dict[str, Any] | None = None,
    logs: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
) -> tuple[Any, dict[str, Any] | None, list[dict[str, Any]] | None, str | None]:
    """Scrub credentials from logs/errors and known secrets from all outputs."""
    secret_values = context._collect_secret_values()
    if logs is not None:
        logs = [
            {**entry, "message": _sanitize_runtime_log_message(entry["message"], secret_values)}
            for entry in logs
        ]
    if error_message is not None:
        error_message = _sanitize_runtime_log_message(error_message, secret_values)
    if not secret_values:
        return result, variables, logs, error_message
    return (
        redact_secrets(result, secret_values) if result is not None else None,
        redact_secrets(variables, secret_values) if variables is not None else None,
        redact_secrets(logs, secret_values) if logs is not None else None,
        redact_secrets(error_message, secret_values) if error_message is not None else None,
    )


async def execute(request: ExecutionRequest) -> ExecutionResult:
    """
    Unified execution engine for all code execution.

    Handles:
    - Direct function execution (request.func set)
    - Inline Python scripts (request.code set)

    Args:
        request: ExecutionRequest with execution details

    Returns:
        ExecutionResult with execution outcome

    Raises:
        ValueError: If neither func nor code provided
    """
    start_time = datetime.now(timezone.utc)

    # Supervised service mode: cooperative stop, no timeout kill, no
    # variable tracing, attempt-scoped log streaming.
    if request.service is not None:
        return await execute_service(request, start_time=start_time)

    # Resolve what we're executing
    func = None
    is_script = False
    is_data_provider = "data_provider" in request.tags

    if request.func and request.code:
        raise ValueError(
            "Cannot provide both func and code - they are mutually exclusive")

    if request.func:
        # Direct function execution (from discovery)
        func = request.func
        is_script = False
    elif request.code:
        # Executing inline script
        is_script = True
        func = None
    else:
        raise ValueError("Must provide either func or code")

    # Create execution context
    from src.sdk.context import ROIContext

    # Initialize ROI from request if provided
    roi = ROIContext()
    if request.roi:
        roi.time_saved = request.roi.get("time_saved", 0)
        roi.value = request.roi.get("value", 0.0)

    from src.config import get_settings

    context = ExecutionContext(
        user_id=request.caller.user_id,
        email=request.caller.email,
        name=request.caller.name,
        scope=request.organization.id if request.organization else "GLOBAL",
        organization=request.organization,
        is_platform_admin=request.is_platform_admin,
        is_function_key=False,  # Engine executions are not function key based
        execution_id=request.execution_id,
        workflow_deadline=request.workflow_deadline,
        workflow_timeout_seconds=request.timeout_seconds,
        workflow_name=request.name or "",  # Workflow/script name for context
        public_url=get_settings().public_url,
        startup=request.startup,  # Launch workflow results (from form execution)
        form_inputs=request.form_inputs,
        embed=request.embed,
        roi=roi,
        event=request.event,
        solution_id=request.solution_id,  # install scope for SDK name lookups
        artifact_workspace_id=request.artifact_workspace_id,
    )

    # The engine token this process installed as its SDK credentials is a
    # secret like any other: register it so _scrub_outputs redacts it from
    # the result, logs, variables and error message.
    context._register_dynamic_secret(request.engine_token)

    # Set bifrost SDK context if available
    if BIFROST_CONTEXT_AVAILABLE:
        set_execution_context(context)
        logger.debug(
            f"Set bifrost execution context for execution {request.execution_id}")

    # NOTE: SDK cache warming is done in the consumer BEFORE dispatching to thread workers.
    # This avoids event loop issues - the consumer runs in a stable main event loop,
    # while thread workers create new event loops via asyncio.run() per execution.

    # Set up stdout/stderr capture
    # Python's logging writes to stderr by default, so we'll capture it naturally
    logger_output: list[dict[str, Any]] = []
    stdout_capture = StringIO()
    stderr_capture = StringIO()
    # For captured logging (scripts and workflows)
    captured_logs: list[str] = []
    captured_variables: dict[str, Any] = {}  # Initialize to empty dict
    cache_expires_at_str: str | None = None  # For data providers

    # SDK authentication uses the pre-minted runtime credential installed by
    # worker.py for this one-shot child.
    # The SDK's get_client() finds these credentials automatically.

    try:
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            # The child has no Redis credentials. The authenticated worker SDK
            # transport derives this execution's workflow, current authority
            # partition, and TTL on the server.
            if is_data_provider and not request.no_cache:
                cached_result = await _read_runtime_data_provider_cache(request.parameters)
                if cached_result:
                    return _build_cached_result(
                        request.execution_id,
                        cached_result,
                        start_time
                    )

            # Convert scripts to callables for unified execution
            if is_script:
                assert request.code is not None
                func = _script_to_callable(request.code, request.name or "script")

            # Unified execution path for both workflows and scripts
            assert func is not None

            # Always capture logs AND variables for all executions
            # Filtering based on permissions happens at API response level
            result, captured_variables, captured_logs = await _execute_workflow_with_trace(
                func,
                context,
                request.parameters,
                execution_id=request.execution_id,
                broadcaster=request.broadcaster
            )

            if is_data_provider and not request.no_cache:
                expires_at = await _write_runtime_data_provider_cache(request.parameters, result)
                if expires_at is not None:
                    cache_expires_at_str = expires_at

        # Process captured logs (from scripts or workflows with platform admin)
        logger.info(
            f"Processing {len(captured_logs)} captured logs for execution {request.execution_id}")
        for log_line in captured_logs:
            # Parse format: [LEVEL] message
            level = 'info'
            message = log_line

            if log_line.startswith('[INFO]'):
                level = 'info'
                message = log_line[6:].strip()
            elif log_line.startswith('[WARNING]') or log_line.startswith('[WARN]'):
                level = 'warning'
                message = log_line[log_line.index(']')+1:].strip()
            elif log_line.startswith('[ERROR]'):
                level = 'error'
                message = log_line[7:].strip()
            elif log_line.startswith('[DEBUG]'):
                level = 'debug'
                message = log_line[7:].strip()
            elif log_line.startswith('[TRACEBACK]'):
                level = 'traceback'
                message = log_line[11:].strip()

            logger_output.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': level,
                'message': message,
                'source': 'script' if is_script else 'workflow'
            })

        # Calculate duration
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Determine execution status based on result
        # If result is a dict with success=false (explicitly false), mark as COMPLETED_WITH_ERRORS
        status = ExecutionStatus.SUCCESS
        if isinstance(result, dict) and result.get('success') is False:
            status = ExecutionStatus.COMPLETED_WITH_ERRORS

        # Capture final ROI from context
        roi_data = {
            "time_saved": context.roi.time_saved,
            "value": context.roi.value,
        }

        scrubbed_result, scrubbed_vars, scrubbed_logs, _ = _scrub_outputs(
            context, result, captured_variables, logger_output
        )

        return ExecutionResult(
            execution_id=request.execution_id,
            status=status,
            result=scrubbed_result,
            duration_ms=duration_ms,
            logs=scrubbed_logs or [],
            variables=scrubbed_vars or {},
            integration_calls=context._integration_calls,
            roi=roi_data,
            execution_context=context.to_public_dict(),
            cached=False,
            cache_expires_at=cache_expires_at_str
        )

    except WorkflowExecutionException as e:
        # Workflow exception with captured variables
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Extract variables and logs from the wrapper exception
        captured_variables = e.captured_vars
        captured_logs = e.logs

        # Process captured logs
        for log_line in captured_logs:
            level = 'info'
            message = log_line

            if log_line.startswith('[INFO]'):
                level = 'info'
                message = log_line[6:].strip()
            elif log_line.startswith('[WARNING]') or log_line.startswith('[WARN]'):
                level = 'warning'
                message = log_line[log_line.index(']')+1:].strip()
            elif log_line.startswith('[ERROR]'):
                level = 'error'
                message = log_line[7:].strip()
            elif log_line.startswith('[DEBUG]'):
                level = 'debug'
                message = log_line[7:].strip()

            logger_output.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': level,
                'message': message,
                'source': 'workflow'
            })

        # Determine error type from original exception
        original_exc = e.original_exception
        error_type = type(original_exc).__name__
        execution_context = "executing script" if request.code else "executing workflow"
        error_message = format_exception_message(
            original_exc,
            context=execution_context,
        )

        # Add traceback to logs
        import traceback
        if isinstance(original_exc, UserError):
            # UserError: Show message only (user-facing)
            logger_output.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': 'error',
                'message': str(original_exc),
                'source': 'workflow'
            })
        else:
            # Other exceptions: Add full traceback
            logger_output.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': 'error',
                'message': f"Execution error: {request.name or 'workflow'}",
                'source': 'workflow'
            })
            tb_lines = traceback.format_exception(type(original_exc), original_exc, original_exc.__traceback__)
            for line in ''.join(tb_lines).split('\n'):
                if line.strip():
                    logger_output.append({
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'level': 'error',
                        'message': line,
                        'source': 'workflow'
                    })

        status = ExecutionStatus.FAILED

        # Free traceback references — already formatted above
        original_exc.__traceback__ = None

        _, scrubbed_vars, scrubbed_logs, scrubbed_err = _scrub_outputs(
            context, None, captured_variables, logger_output, error_message
        )

        return ExecutionResult(
            execution_id=request.execution_id,
            status=status,
            result=None,
            duration_ms=duration_ms,
            logs=scrubbed_logs or [],
            variables=scrubbed_vars or {},
            integration_calls=context._integration_calls,
            execution_context=context.to_public_dict(),
            error_message=scrubbed_err,
            error_type=error_type
        )

    except WorkflowError as e:
        # Expected workflow error (without variable capture)
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Add traceback to logs
        import traceback
        logger_output.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'error',
            'message': f"Workflow error: {request.name or 'workflow'}",
            'source': 'workflow'
        })
        tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
        for line in ''.join(tb_lines).split('\n'):
            if line.strip():
                logger_output.append({
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'level': 'error',
                    'message': line,
                    'source': 'workflow'
                })

        _wf_err_msg = format_exception_message(
            e,
            context="executing script" if request.code else "executing workflow",
        )
        e.__traceback__ = None  # Free traceback references

        _, scrubbed_vars, scrubbed_logs, scrubbed_err = _scrub_outputs(
            context, None, captured_variables, logger_output, _wf_err_msg
        )

        return ExecutionResult(
            execution_id=request.execution_id,
            status=ExecutionStatus.FAILED,
            result=None,
            duration_ms=duration_ms,
            logs=scrubbed_logs or [],
            variables=scrubbed_vars or {},
            integration_calls=context._integration_calls,
            execution_context=context.to_public_dict(),
            error_message=scrubbed_err,
            error_type=type(e).__name__
        )

    except Exception as e:
        # Unexpected error
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        execution_context = "executing script" if request.code else "executing workflow"
        error_message = format_exception_message(
            e,
            context=execution_context,
        )

        with _runtime_logging_scope(logging.getLogger(), context._collect_secret_values):
            logger.error(
                f"Execution error: {request.name or 'script'}",
                extra={
                    "execution_id": request.execution_id,
                    "error": _sanitize_runtime_log_message(error_message, context._collect_secret_values()),
                    "error_type": type(e).__name__
                },
                exc_info=True
            )

        # Add traceback to logs
        import traceback
        if isinstance(e, UserError):
            # UserError: Show message only (user-facing)
            logger_output.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': 'error',
                'message': str(e),
                'source': 'script' if request.code else 'workflow'
            })
        else:
            # Other exceptions: Add full traceback
            logger_output.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'level': 'error',
                'message': f"Execution error: {request.name or 'script'}",
                'source': 'script' if request.code else 'workflow'
            })
            tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
            for line in ''.join(tb_lines).split('\n'):
                if line.strip():
                    logger_output.append({
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'level': 'error',
                        'message': line,
                        'source': 'script' if request.code else 'workflow'
                    })

        _gen_err_msg = error_message
        e.__traceback__ = None  # Free traceback references

        _, scrubbed_vars, scrubbed_logs, scrubbed_err = _scrub_outputs(
            context, None, captured_variables, logger_output, _gen_err_msg
        )

        return ExecutionResult(
            execution_id=request.execution_id,
            status=ExecutionStatus.FAILED,
            result=None,
            duration_ms=duration_ms,
            logs=scrubbed_logs or [],
            variables=scrubbed_vars or {},
            integration_calls=context._integration_calls,
            execution_context=context.to_public_dict(),
            error_message=scrubbed_err,
            error_type=type(e).__name__
        )

    finally:
        # NOTE: flush_logs_to_postgres and cleanup_execution_cache
        # are now called from workflow_execution.py's _process_execution_result() callback.
        # This avoids event loop issues when running in thread workers (each thread creates
        # its own event loop via asyncio.run(), which breaks shared async Redis connections).
        # The consumer callback runs in the stable main event loop.

        # Clear bifrost SDK context
        if BIFROST_CONTEXT_AVAILABLE:
            clear_execution_context()
            logger.debug(
                f"Cleared bifrost execution context for execution {request.execution_id}")

        # Close broadcaster to prevent resource leaks
        if request.broadcaster is not None:
            try:
                request.broadcaster.close()
                logger.debug(f"Closed broadcaster for execution {request.execution_id}")
            except Exception as e:
                logger.warning(f"Error closing broadcaster: {e}")


async def execute_service(
    request: ExecutionRequest,
    start_time: datetime | None = None,
) -> ExecutionResult:
    """Run one supervised @service attempt to a terminal outcome.

    Service-mode semantics (vs one-shot workflows):
    - the service coroutine runs indefinitely until it returns, raises, or
      a stop is requested — no timeout kill, no variable
      capture, no completion Postgres flush;
    - stop arrives cooperatively: SIGTERM/SIGINT sets the supervision event
      (installed on the loop, overriding the pool's loop-top flag handler)
      and the parent mirrors ``stop_requested`` over a Redis key as backup;
    - logs stream continuously to the attempt-scoped Redis stream
      (``publish_service_log``) with the same MAXLEN bound as executions;
    - ``service.ready()`` reports are drained to the parent's Redis ready key
      and the renewable credential is refreshed from the parent's handoff.
    """
    from src.config import get_settings

    cfg = request.service
    assert cfg is not None
    start_time = start_time or datetime.now(timezone.utc)
    func = request.func
    if func is None:
        raise ValueError("Service execution requires request.func")
    metadata = getattr(func, "_executable_metadata", None)
    if metadata is not None and getattr(metadata, "type", None) != "service":
        raise ValueError(
            f"Service execution requires a @service function, got type="
            f"{getattr(metadata, 'type', None)!r}"
        )

    from src.sdk.context import ROIContext

    roi = ROIContext()
    if request.roi:
        roi.time_saved = request.roi.get("time_saved", 0)
        roi.value = request.roi.get("value", 0.0)

    context = ExecutionContext(
        user_id=request.caller.user_id,
        email=request.caller.email,
        name=request.caller.name,
        scope=request.organization.id if request.organization else "GLOBAL",
        organization=request.organization,
        is_platform_admin=request.is_platform_admin,
        is_function_key=False,
        execution_id=cfg.attempt_id,
        workflow_name=request.name or "",
        public_url=get_settings().public_url,
        startup=request.startup,
        form_inputs=request.form_inputs,
        embed=request.embed,
        roi=roi,
        event=request.event,
        solution_id=request.solution_id,
        artifact_workspace_id=request.artifact_workspace_id,
    )

    if BIFROST_CONTEXT_AVAILABLE:
        set_execution_context(context)

    # Install the initial renewable credential handed down at dispatch.
    if cfg.token:
        from bifrost._service_runtime import install_service_credentials

        install_service_credentials(cfg.token)

    stop_event = asyncio.Event()
    from bifrost._service_runtime import (
        clear_service_runtime,
        install_service_runtime,
    )

    install_service_runtime(stop_event)

    # Cooperative stop: SIGTERM/SIGINT resolves wait_until_stopping() even
    # when user code is parked in an await (the pool's loop-top SIGTERM flag
    # alone cannot interrupt that). Guarded for non-Unix loops.
    loop = asyncio.get_running_loop()
    installed_signals: list[int] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            # Non-Unix loop or unsupported signal: cooperative stop still
            # works via the stop-event mirror; signals are best-effort.
            pass

    supervisor_task: asyncio.Task[None] | None = None
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            supervisor_task = asyncio.create_task(
                _service_supervisor(cfg, stop_event),
                name=f"service-supervisor-{cfg.attempt_id[:8]}",
            )
            service_logger = logging.getLogger(
                getattr(func, "__module__", __name__)
            )
            service_logger.info(
                "service starting (revision %s)", cfg.revision or "unknown"
            )
            # The parent decides the completion reason from durable state
            # (stop_requested_at set → 'requested'); the local flag only
            # records that this run unwound via the stop path.
            result, service_logs, _ = await _execute_service_with_trace(
                func, context, request.parameters or {}, cfg, stop_event
            )

        duration_ms = int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        )
        return ExecutionResult(
            execution_id=cfg.attempt_id,
            status=ExecutionStatus.SUCCESS,
            result=result,
            duration_ms=duration_ms,
            logs=service_logs,
            variables=None,
            integration_calls=context._integration_calls,
            execution_context=None,
            error_message=None,
            error_type=None,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        duration_ms = int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        )
        service_logger = logging.getLogger(getattr(func, "__module__", __name__))
        # The user-facing log line is scrubbed by the log handler, but the
        # persisted error string is built here: redact fetched secrets or
        # they land verbatim in ServiceAttempt.error via the attempts API.
        secret_values = context._collect_secret_values()
        raw_message = format_exception_message(e)
        error_message = _sanitize_runtime_log_message(raw_message, secret_values)
        service_logger.error("service failed: %s", error_message)
        e.__traceback__ = None
        return ExecutionResult(
            execution_id=cfg.attempt_id,
            status=ExecutionStatus.FAILED,
            result=None,
            duration_ms=duration_ms,
            logs=[],
            variables=None,
            integration_calls=[],
            execution_context=None,
            error_message=error_message,
            error_type=type(e).__name__,
        )
    finally:
        if supervisor_task is not None:
            supervisor_task.cancel()
            try:
                await supervisor_task
            except asyncio.CancelledError:
                # Expected: we just cancelled it and awaited shutdown.
                pass
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                # Handler was never installed (see install guard above).
                pass
        clear_service_runtime()
        if BIFROST_CONTEXT_AVAILABLE:
            clear_execution_context()


async def _service_supervisor(
    cfg: ServiceRunConfig, stop_event: asyncio.Event
) -> None:
    """Exchange only this attempt's control state over its authenticated API."""
    from bifrost._service_runtime import install_service_credentials, take_ready_report
    from bifrost.client import get_client, raise_for_status_with_detail

    ready_pending = False
    while not stop_event.is_set():
        try:
            ready_pending = ready_pending or take_ready_report()
            response = await get_client().engine_request(
                "POST", "/api/sdk/runtime/service-control",
                json={"ready": ready_pending, "token_expires_at": cfg.token_expires_at},
            )
            if response.status_code in {401, 403}:
                stop_event.set()
                break
            raise_for_status_with_detail(response)
            payload = response.json()
            ready_pending = False
            if payload["stop_requested"]:
                stop_event.set()
                break
            if payload["access_token"] is not None:
                install_service_credentials(payload["access_token"])
                cfg.token_expires_at = payload["token_expires_at"]
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("service supervisor tick failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue


async def _execute_service_with_trace(
    func: Any,
    context: ExecutionContext,
    parameters: dict[str, Any],
    cfg: ServiceRunConfig,
    stop_event: asyncio.Event,
) -> tuple[Any, list[dict[str, Any]], bool]:
    """Invoke the @service coroutine with log capture and stop supervision.

    Returns (result, service_logs, stopped). No variable tracing: services
    never persist ``variables``/``execution_context`` snapshots.
    """
    from bifrost._runtime_io import write_log

    service_logs: list[dict[str, Any]] = []
    workflow_module_file = func.__code__.co_filename

    class ServiceLogHandler(logging.Handler):
        def filter(self, record: logging.LogRecord) -> bool:
            if getattr(record, "_bifrost_log_transport", False):
                return False
            workflow_basename = os.path.basename(workflow_module_file)
            record_basename = os.path.basename(record.pathname)
            is_workspace_log = (
                "workspace" in record.pathname or "/repo/" in record.pathname
            )
            # System rows (starting/ready/stopping) are emitted by the
            # engine itself: same logger name as the service module but an
            # engine pathname. They are first-class service log entries.
            is_service_system = record.name == getattr(
                func, "__module__", None
            )
            # Reject infrastructure logs before Handler.handle acquires its lock.
            # Transport refresh may log from a thread while a service log is sent.
            return (
                record_basename == workflow_basename
                or is_workspace_log
                or is_service_system
            )

        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            current_secrets = context._collect_secret_values()
            message = _sanitize_runtime_log_message(message, current_secrets)
            service_logs.append(
                {"level": record.levelname, "message": message}
            )
            try:
                write_log(record.levelname, message)
            except Exception as e:
                logger.debug("service log publish failed: %s", e, extra={"_bifrost_log_transport": True})

    handler = ServiceLogHandler()
    handler.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()
    logging_scope = ExitStack()
    logging_scope.enter_context(
        _runtime_logging_scope(root_logger, context._collect_secret_values)
    )
    root_logger.addHandler(handler)
    try:
        # System rows are first-class service log entries: starting is emitted
        # here so the handler above captures it like every later row.
        logging.getLogger(getattr(func, "__module__", __name__)).info(
            "service starting (revision %s)", cfg.revision or "unknown"
        )
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        accepted_param_names = {p.name for p in params}
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params
        )
        if has_var_keyword:
            accepted_params = _coerce_params_to_type_hints(func, parameters)
        else:
            accepted_params = _coerce_params_to_type_hints(
                func,
                {
                    k: v
                    for k, v in parameters.items()
                    if k in accepted_param_names
                },
            )
        context.parameters = dict(parameters)

        first_param_is_context = False
        if params:
            first_param = params[0]
            annotation = first_param.annotation
            if annotation is not inspect.Parameter.empty:
                if annotation is ExecutionContext:
                    first_param_is_context = True
                elif (
                    isinstance(annotation, str)
                    and "ExecutionContext" in annotation
                ):
                    first_param_is_context = True
            if not first_param_is_context and first_param.name == "context":
                first_param_is_context = True

        async def _run_service_async():
            if first_param_is_context:
                return await func(context, **accepted_params)
            return await func(**accepted_params)

        user_task = asyncio.ensure_future(_run_service_async())
        stop_waiter = asyncio.ensure_future(stop_event.wait())
        try:
            await asyncio.wait(
                {user_task, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not stop_waiter.done():
                stop_waiter.cancel()

        # A set stop event at completion time owns the outcome — even when
        # the user coroutine returned in the same tick. The parent completes
        # stop-requested attempts as 'requested' no matter how the child
        # exited, so the racing value is dropped and the run reports stopped.
        if stop_event.is_set():
            service_logger = logging.getLogger(func.__module__)
            service_logger.info("service stopping")
            if not user_task.done():
                user_task.cancel()
                try:
                    await user_task
                except asyncio.CancelledError:
                    # Expected: we just cancelled it and awaited shutdown.
                    pass
            return None, service_logs, True

        return user_task.result(), service_logs, False
    finally:
        root_logger.removeHandler(handler)
        logging_scope.close()


def _script_to_callable(code: str, name: str):
    """
    Convert base64-encoded script code to an async callable function.

    This allows scripts to be executed through the same unified execution path
    as workflows, enabling consistent logging, variable capture, and real-time
    streaming.

    The script code is wrapped in an async main() function to enable:
    - Variable capture when main() returns
    - Function-level logging that can be filtered
    - Support for both sync and async code

    Args:
        code: Base64-encoded Python code
        name: Script name for logging and error messages

    Returns:
        Async callable function with signature: async def(context, **parameters)
    """
    import base64
    import textwrap

    # Decode base64 code
    script_code = base64.b64decode(code).decode('utf-8')

    # Wrap script code in an async main() function whose locals are captured
    # when it returns
    # Add return statement to capture 'result' variable if set
    wrapped_code = f"""async def main():
{textwrap.indent(script_code, '    ')}
    return result if 'result' in dir() else None
"""

    # Compile wrapped script
    compiled_code = compile(wrapped_code, f'<script:{name}>', 'exec')
    main_code = next(
        const for const in compiled_code.co_consts
        if isinstance(const, CodeType) and const.co_name == 'main'
    )

    async def script_wrapper(context: ExecutionContext, **parameters):
        """
        Async wrapper that executes the script code.

        The script code runs inside an async main() function, allowing:
        - variable capture from main's frame when it returns
        - await to work in user scripts
        - Proper logging with script-specific logger
        """
        import sys
        import types

        # Create script-specific logger
        script_logger = logging.getLogger(f'script.{name}')
        script_logger.setLevel(logging.DEBUG)
        script_logger.propagate = True

        # Create a custom logging module wrapper that uses our script logger
        # This allows scripts to use logging.info() and have it route to script.{name} logger
        script_logging = types.ModuleType('logging')

        # Copy all attributes from real logging module
        for attr in dir(logging):
            if not attr.startswith('_'):
                setattr(script_logging, attr, getattr(logging, attr))

        # Override the module-level logging functions to use our script logger
        # Using setattr for type-checker compatibility with ModuleType
        setattr(script_logging, 'debug', script_logger.debug)
        setattr(script_logging, 'info', script_logger.info)
        setattr(script_logging, 'warning', script_logger.warning)
        setattr(script_logging, 'error', script_logger.error)
        setattr(script_logging, 'critical', script_logger.critical)
        setattr(script_logging, 'exception', script_logger.exception)
        setattr(script_logging, 'log', script_logger.log)
        def get_script_logger(name: str | None = None) -> logging.Logger:
            # Third-party modules imported by a script bind this logger during
            # import, so transport families must retain their real identities.
            if name is not None and _is_runtime_transport_logger(name):
                return logging.getLogger(name)
            return script_logger

        setattr(script_logging, 'getLogger', get_script_logger)

        # Temporarily replace logging in sys.modules so imports get our wrapper
        original_logging = sys.modules.get('logging')
        sys.modules['logging'] = script_logging

        exec_globals: dict[str, Any] = {}
        try:
            # Create execution namespace with bifrost SDK access
            exec_globals = {
                '__name__': '__main__',
                '__file__': f'<script:{name}>',
                'context': context,
                'logging': script_logging,  # Provide wrapped logging module
                'logger': script_logger,  # Also provide logger instance for direct use
                **parameters  # Make parameters available as globals
            }

            # Execute wrapped script (defines main() function)
            exec(compiled_code, exec_globals)

            # Call main() from async context and capture return value
            main_result = await exec_globals['main']()

            # Return main's result if set, otherwise return success message
            return main_result if main_result is not None else {"status": "completed", "message": "Script executed successfully"}
        finally:
            # Restore original logging module
            if original_logging is not None:
                sys.modules['logging'] = original_logging
            else:
                sys.modules.pop('logging', None)
            # Clear exec namespace to release references to context, params, user objects
            exec_globals.clear()

    # Set function metadata for logging and error messages
    script_wrapper.__name__ = name
    script_wrapper.__module__ = f'<script:{name}>'
    setattr(script_wrapper, _CAPTURE_CODE_ATTR, main_code)

    return script_wrapper


async def _execute_workflow_with_trace(
    func: Any,
    context: ExecutionContext,
    parameters: dict[str, Any],
    execution_id: str | None = None,
    broadcaster: Any = None
) -> tuple[Any, dict[str, Any], list[str]]:
    """
    Execute a workflow function, capturing its local variables.

    This is the same approach used for scripts, ensuring consistency.
    Captures the function's local variables when it returns, or from the
    traceback when it raises.
    Streams logs in real-time via SignalR if broadcaster is provided.

    Args:
        func: Workflow function to execute
        context: ExecutionContext
        parameters: Function parameters
        execution_id: Execution ID for Web PubSub broadcasts
        broadcaster: WebPubSubBroadcaster for real-time log streaming

    Returns:
        Tuple of (result, captured_variables, logs)
    """
    captured_vars: dict[str, Any] = {}
    workflow_logs: list[str] = []
    func_name = func.__name__
    log_buffer: list[dict[str, Any]] = []  # Buffer for SignalR broadcasts

    # Thread-safe sequence counter for log ordering in WebPubSub broadcasts
    # Each execution gets its own counter starting at 0
    import threading
    broadcast_sequence_lock = threading.Lock()
    broadcast_sequence_counter = 0

    # Get the workflow's module file path for filtering
    workflow_module_file = func.__code__.co_filename

    # Known noisy third-party loggers that should never appear in workflow logs
    _NOISY_LOGGERS = frozenset({
        "httpcore", "httpx", "urllib3", "botocore", "boto3",
        "asyncio", "aiohttp", "aiobotocore", "s3transfer",
        "charset_normalizer", "hpack",
    })

    # Set up logging capture for the workflow
    class WorkflowLogHandler(logging.Handler):
        def filter(self, record: logging.LogRecord) -> bool:
            if getattr(record, "_bifrost_log_transport", False):
                return False
            if record.name.split(".")[0] in _NOISY_LOGGERS:
                return False
            is_traceback = record.levelname == "TRACEBACK"
            is_script_log = record.name.startswith("script.")
            workflow_basename = os.path.basename(workflow_module_file)
            record_basename = os.path.basename(record.pathname)
            is_workspace_log = "workspace" in record.pathname or "/repo/" in record.pathname
            # Keep transport/internal logs outside the handler lock, including
            # logs emitted on a credential-refresh thread while sending a row.
            return (
                is_traceback or is_script_log
                or record_basename == workflow_basename or is_workspace_log
            )

        def emit(self, record: logging.LogRecord) -> None:
            # Format: [LEVEL] message
            message = record.getMessage()
            _current_secrets = context._collect_secret_values()
            message = _sanitize_runtime_log_message(message, _current_secrets)
            log_entry = f"[{record.levelname}] {message}"
            workflow_logs.append(log_entry)

            # Real-time log processing - stream to Redis pub/sub for WebSocket delivery
            if execution_id:
                import uuid
                log_id = str(uuid.uuid4())
                timestamp = datetime.now(timezone.utc).isoformat() + "Z"

                # Atomically get next sequence number BEFORE spawning thread
                # This ensures sequence assignment order matches log order
                nonlocal broadcast_sequence_counter
                with broadcast_sequence_lock:
                    broadcast_sequence_counter += 1
                    sequence = broadcast_sequence_counter

                log_dict = {
                    "executionLogId": log_id,
                    "level": record.levelname,
                    "message": message,
                    "timestamp": timestamp,
                    "sequence": sequence
                }
                log_buffer.append(log_dict)

                # Unified log streaming: Write to Redis Stream + publish to PubSub
                # Redis Stream is the single source of truth for logs
                # - PubSub delivers immediately to WebSocket clients
                # - Background worker persists from Stream to Postgres
                try:
                    write_log(level=record.levelname, message=message)
                except Exception as e:
                    # Log errors but don't fail workflow execution
                    # Real-time updates are non-critical
                    logger.error(
                        f"Failed to stream log (non-fatal): {str(e)}",
                        exc_info=True,
                        extra={"execution_id": execution_id, "_bifrost_log_transport": True}
                    )

    # Configure logging for workflows
    handler = WorkflowLogHandler()
    handler.setLevel(logging.DEBUG)  # Capture all levels

    # Attach to root logger since workflows use logging.info() which goes to root
    root_logger = logging.getLogger()
    logging_scope = ExitStack()
    logging_scope.enter_context(
        _runtime_logging_scope(root_logger, context._collect_secret_values)
    )
    root_logger.addHandler(handler)

    def remove_circular_refs(obj, seen=None):
        """
        Remove circular references from objects to make them JSON serializable.

        Recursively walks through dicts/lists/tuples and replaces circular references
        with a string marker. Non-serializable objects are converted to string repr.
        """
        if seen is None:
            seen = set()

        obj_id = id(obj)
        if obj_id in seen:
            return "[Circular Reference]"

        seen = seen.copy()  # Copy seen set for this branch
        seen.add(obj_id)

        try:
            # Redact SecretString before serialization — json.dumps bypasses __str__
            from src.core.secret_string import SecretString, REDACTED
            if isinstance(obj, SecretString):
                return REDACTED

            if isinstance(obj, dict):
                return {k: remove_circular_refs(v, seen) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                cleaned = [remove_circular_refs(item, seen) for item in obj]
                return cleaned if isinstance(obj, list) else tuple(cleaned)
            elif isinstance(obj, set):
                return [remove_circular_refs(item, seen) for item in obj]
            elif isinstance(obj, BaseModel):
                # Convert Pydantic models to dict and recursively clean
                return remove_circular_refs(obj.model_dump(), seen)
            else:
                # Test if primitive type is JSON serializable
                json.dumps(obj)
                return obj
        except (TypeError, ValueError):
            # Not serializable - return type name
            return f"<{type(obj).__name__}>"

    # Max size per variable before truncation (10KB)
    MAX_VARIABLE_SIZE = 10_000

    # Helper to capture variables from locals
    def capture_variables_from_locals(local_vars: dict[str, Any]) -> None:
        """Capture variables from a frame's local variables, excluding params and internals."""
        param_names = set(parameters.keys()) | {'context', 'self'}

        for k, v in local_vars.items():
            # Skip parameters, private vars, callables, and modules
            if (not k.startswith('_')
                and k not in param_names
                and not callable(v)
                and not isinstance(v, type(sys))):
                # Remove circular references to make JSON serializable
                cleaned_value = remove_circular_refs(v)
                # Truncate large values to avoid freezing the UI
                try:
                    serialized = json.dumps(cleaned_value, default=str)
                    if len(serialized) > MAX_VARIABLE_SIZE:
                        size_desc = _human_size(len(serialized))
                        captured_vars[k] = f"<{type(v).__name__}: {size_desc}, truncated>"
                    else:
                        captured_vars[k] = cleaned_value
                except (TypeError, ValueError):
                    captured_vars[k] = f"<{type(v).__name__}>"

    exception_to_raise = None

    # Successful runs capture locals when the workflow (or a script's main())
    # returns. The outer exception handler captures them from the traceback on
    # failures. Unwrap functools.wraps decorators so the user's function, not
    # a wrapper such as a retry decorator, is the one observed.
    capture_code = getattr(func, _CAPTURE_CODE_ATTR, None) or inspect.unwrap(func).__code__

    try:
        # Inspect function signature to determine if it expects context parameter
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        # Get accepted parameter names from the function signature
        # This allows us to split form fields into accepted params vs extra params
        accepted_param_names = {p.name for p in params}

        # Check if function accepts **kwargs (VAR_KEYWORD) - if so, all params are accepted
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params
        )

        if has_var_keyword:
            # Function accepts **kwargs, pass everything
            # Still coerce types for known parameters
            accepted_params = _coerce_params_to_type_hints(func, parameters)
        else:
            # Only pass parameters that match function signature
            accepted_params = {
                k: v for k, v in parameters.items() if k in accepted_param_names
            }
            # Coerce string parameters to expected types (for GET query params)
            accepted_params = _coerce_params_to_type_hints(func, accepted_params)

        # Store ALL params in context.parameters for consistent access
        # Workflows can access any input via: context.parameters.get('param_name')
        # This includes both signature-matched params and extra params
        context.parameters = dict(parameters)
        # Also add to captured_vars so they appear in execution details
        for key, value in parameters.items():
            captured_vars[key] = remove_circular_refs(value)

        # Check if first parameter is for context (by type annotation OR by name as fallback)
        first_param_is_context = False
        if params:
            first_param = params[0]
            annotation = first_param.annotation

            # Check type annotation first (preferred - supports any parameter name)
            if annotation is not inspect.Parameter.empty:
                if annotation is ExecutionContext:
                    first_param_is_context = True
                elif isinstance(annotation, str) and 'ExecutionContext' in annotation:
                    first_param_is_context = True

            # Fallback: check if parameter is named 'context' (for untyped legacy workflows)
            if not first_param_is_context and first_param.name == 'context':
                first_param_is_context = True

        # Define the workflow execution function
        async def _run_workflow_async():
            if first_param_is_context:
                return await func(context, **accepted_params)
            else:
                return await func(**accepted_params)

        # Set up execution context for SDK support
        if BIFROST_CONTEXT_AVAILABLE:
            set_execution_context(context)

        try:
            with _capture_locals_on_return(capture_code, capture_variables_from_locals):
                # Run the workflow directly - isolation is provided by subprocess
                result = await _run_workflow_async()
        finally:
            # Clear context
            if BIFROST_CONTEXT_AVAILABLE:
                clear_execution_context()
    except TypeError as e:
        # Check if this is the "got multiple values" error caused by missing context parameter
        if "got multiple values for argument" in str(e):
            # This likely means the workflow is missing the context parameter
            # The engine passed context as first positional arg, and it got bound to the first param,
            # then the same param name was passed as a keyword arg
            workflow_logger = logging.getLogger(func.__module__)
            workflow_logger.error(
                f"Workflow function '{func_name}' is missing the 'context' parameter. "
                f"All @workflow decorated functions should have 'context: ExecutionContext' "
                f"as the first parameter (or omit it entirely if only using SDK functions)."
            )
            # Wrap and re-raise with helpful message
            captured_vars = {}
            exception_to_raise = WorkflowExecutionException(e, captured_vars, workflow_logs)
            result = None
        else:
            # Different TypeError - handle normally
            raise
    except Exception as e:
        # On exception, extract variables from the traceback
        # Find the workflow/script function frame in the traceback
        tb_frame = e.__traceback__
        while tb_frame:
            frame_name = tb_frame.tb_frame.f_code.co_name
            # Look for workflow function or script's main() function
            if frame_name in (func_name, 'main'):
                # Found the workflow/script function frame - capture variables
                capture_variables_from_locals(tb_frame.tb_frame.f_locals)
                break
            tb_frame = tb_frame.tb_next

        # Log the error through the logger so it gets streamed in real-time
        workflow_logger = logging.getLogger(func.__module__)
        if isinstance(e, UserError):
            # UserError: Log just the message (user-facing) as ERROR
            workflow_logger.error(str(e))
        else:
            # Other exceptions: Log user-facing error, then traceback at TRACEBACK level
            # First log a generic user-facing error message
            workflow_logger.error("An error occurred during execution")

            # Then log the full traceback with custom TRACEBACK level (only visible to admins)
            import traceback
            # Add custom TRACEBACK level (between ERROR=40 and CRITICAL=50)
            TRACEBACK = 45
            logging.addLevelName(TRACEBACK, "TRACEBACK")

            tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
            for line in ''.join(tb_lines).split('\n'):
                if line.strip():
                    workflow_logger.log(TRACEBACK, line)

        # Wrap exception with captured variables and logs
        exception_to_raise = WorkflowExecutionException(e, captured_vars, workflow_logs)
        result = None
        # Break traceback reference chain to free frame locals — the traceback
        # has already been formatted and logged above, so the chain is no longer needed.
        e.__traceback__ = None
    finally:
        # Clean up the logging handler
        root_logger.removeHandler(handler)
        logging_scope.close()
        # Note: Extra params are now stored in context.parameters instead of being
        # injected into func.__globals__, so no cleanup needed here

    # Re-raise exception if one occurred (after cleanup and variable capture)
    if exception_to_raise:
        raise exception_to_raise

    return result, captured_vars, workflow_logs


async def _read_runtime_data_provider_cache(
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    """Read this child workflow's cache entry through its runtime credential."""
    from bifrost.client import get_client

    try:
        response = await get_client().engine_request(
            "POST", "/api/sdk/runtime/data-provider-cache/read", json={"parameters": parameters},
        )
        if not response.is_success:
            logger.debug("Data-provider cache read was unavailable: HTTP %s", response.status_code)
            return None
        payload = response.json()
        if not payload.get("hit"):
            return None
        expires_at = payload.get("expires_at")
        return {"data": payload.get("result"), "expires_at": expires_at}
    except Exception as exc:
        logger.debug("Data-provider cache read was unavailable: %s", type(exc).__name__)
        return None


async def _write_runtime_data_provider_cache(
    parameters: dict[str, Any], result: Any,
) -> str | None:
    """Write this child workflow's result through its runtime credential."""
    from bifrost.client import get_client

    try:
        response = await get_client().engine_request(
            "POST", "/api/sdk/runtime/data-provider-cache/write",
            json={"parameters": parameters, "result": result},
        )
        if not response.is_success:
            logger.debug("Data-provider cache write was unavailable: HTTP %s", response.status_code)
            return None
        expires_at = response.json().get("expires_at")
        return None if expires_at is None else str(expires_at)
    except Exception as exc:
        logger.debug("Data-provider cache write was unavailable: %s", type(exc).__name__)
        return None


def _build_cached_result(
    execution_id: str,
    cached_entry: dict[str, Any],
    start_time: datetime
) -> ExecutionResult:
    """
    Build ExecutionResult from cached data provider result.

    Args:
        execution_id: Execution ID
        cached_entry: Cached entry with 'data' and 'expires_at' (string) keys
        start_time: Execution start time

    Returns:
        ExecutionResult with cached data
    """
    end_time = datetime.now(timezone.utc)
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    # expires_at comes as ISO string from Redis cache
    expires_at = cached_entry.get('expires_at', '')
    if not expires_at.endswith('Z') and '+' not in expires_at:
        expires_at = expires_at + 'Z'

    return ExecutionResult(
        execution_id=execution_id,
        status=ExecutionStatus.SUCCESS,
        result=cached_entry['data'],
        duration_ms=duration_ms,
        logs=[],
        variables=None,
        integration_calls=[],
        cached=True,
        cache_expires_at=expires_at,
        execution_context=None
    )
