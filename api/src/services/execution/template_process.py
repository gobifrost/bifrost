"""
Template Process for Fork-Based Worker Pool.

A single-threaded process that pre-loads all heavy dependencies and forks
children on request. Children share the template's memory pages via
copy-on-write (COW), drastically reducing per-worker memory overhead.

The template process NEVER:
- Starts an asyncio event loop
- Opens Redis/DB connections
- Spawns background threads
- Initializes thread-based logging handlers

This ensures clean fork behavior (no inherited locked mutexes).

Two profiles share the fork protocol:
- ``workflow`` preloads the execution engine and scrubs platform credentials
  before forking one-shot workflow children.
- ``agent`` preloads the agent runtime, keeps platform credentials (the agent
  consumer opens database sessions and decrypts model keys), freezes the
  collector before accepting forks, and forks one child per ``agent.run``.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
from contextlib import suppress
from multiprocessing.connection import Connection
from typing import Any, Literal, get_args

logger = logging.getLogger(__name__)

# How long a template may take to import and warm before it reports ready.
TEMPLATE_START_TIMEOUT_SECONDS = 120


class _SendQueue:
    """
    Wraps a write-only Connection to provide a queue-like .put() interface.

    Used for the work queue (consumer → child direction).
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def put(self, item: Any) -> None:
        """Send an item to the child."""
        self._conn.send(item)

    def put_nowait(self, item: Any) -> None:
        """Send an item to the child (non-blocking alias for put)."""
        self._conn.send(item)

    def close(self) -> None:
        try:
            self._conn.close()
        except (OSError, BrokenPipeError) as e:
            # Connection already closed or broken — close is idempotent
            logger.debug(f"_SendQueue.close ignoring: {e}")


class _RecvQueue:
    """
    Wraps a read-only Connection to provide a queue-like .get() interface.

    Used for the result queue (child → consumer direction).
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """Receive an item from the child."""
        from queue import Empty
        if timeout is not None:
            if not self._conn.poll(timeout):
                raise Empty
        elif not block:
            if not self._conn.poll(0):
                raise Empty
        return self._conn.recv()

    def get_nowait(self) -> Any:
        """Receive an item without blocking."""
        return self.get(block=False)

    def fileno(self) -> int:
        """Return the result pipe descriptor for event-loop readiness watches."""
        return self._conn.fileno()

    def close(self) -> None:
        try:
            self._conn.close()
        except (OSError, BrokenPipeError) as e:
            # Connection already closed or broken — close is idempotent
            logger.debug(f"_RecvQueue.close ignoring: {e}")

TemplateProfile = Literal["workflow", "agent"]

# Commands sent from consumer to template via pipe
CMD_FORK = "fork"
CMD_GET_EXIT_STATUSES = "get_exit_statuses"
CMD_SHUTDOWN = "shutdown"


def _clear_pre_fork_source_clients() -> None:
    """Drop parent source-loader clients before a child inherits template memory."""
    from src.core.module_cache_sync import _close_http_client, reset_sync_redis

    _close_http_client()
    reset_sync_redis()


def _reap_children(exit_statuses: dict[int, int]) -> None:
    """Reap exited fork children and retain their exit status for the consumer."""
    while True:
        try:
            child_pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        except InterruptedError:
            continue

        if child_pid == 0:
            return

        exit_statuses[child_pid] = os.waitstatus_to_exitcode(status)


def _template_main(
    pipe: Connection,
    profile: TemplateProfile = "workflow",
) -> None:
    """
    Entry point for the template process.

    Loads the profile's heavy dependencies, then waits for fork commands on
    the pipe. This function runs in the template subprocess after the
    entrypoint restores multiprocessing spawn semantics.

    Args:
        pipe: Connection to receive commands from and send responses to consumer.
        profile: ``workflow`` (scrubbed execution engine) or ``agent``
            (agent runtime with platform credentials).
    """
    # Configure logging (no thread-based handlers)
    logging.basicConfig(
        level=logging.INFO,
        format="[template] %(levelname)s - %(message)s",
    )

    logger.info(f"Template process starting (PID={os.getpid()}, profile={profile})")

    if profile == "agent":
        ready_fields = _load_agent_runtime(pipe)
    else:
        ready_fields = _load_workflow_runtime(pipe)
    if ready_fields is None:
        return

    if profile == "agent":
        # Move everything loaded so far to the permanent generation. A child's
        # collections then never write to the shared objects' GC headers, so
        # their pages stay shared with this template instead of being copied
        # into every run. Collect first so garbage is not frozen with them.
        gc.collect()
        gc.freeze()

    logger.info("Template process ready — all dependencies loaded")
    pipe.send({
        "status": "ready",
        "pid": os.getpid(),
        "start_method": multiprocessing.get_start_method(),
        "process_name": multiprocessing.current_process().name,
        "frozen_objects": gc.get_freeze_count(),
        **ready_fields,
    })

    # ----- Fork loop -----
    # Single-threaded, no event loop. Just wait for commands and fork.
    exit_statuses: dict[int, int] = {}
    while True:
        _reap_children(exit_statuses)

        try:
            if not pipe.poll(timeout=1.0):
                continue

            cmd = pipe.recv()
        except (EOFError, OSError):
            # Consumer closed the pipe — shut down
            logger.info("Template pipe closed, shutting down")
            break

        if cmd.get("action") == CMD_SHUTDOWN:
            logger.info("Template received shutdown command")
            break

        if cmd.get("action") == CMD_GET_EXIT_STATUSES:
            # A child may have exited after the sweep at the top of the loop.
            _reap_children(exit_statuses)
            pipe.send({
                "status": "exit_statuses",
                "exit_statuses": list(exit_statuses.items()),
            })
            exit_statuses.clear()
            continue

        if cmd.get("action") == CMD_FORK:
            worker_id = cmd.get("worker_id", "unknown")
            persistent = cmd.get("persistent", False)
            work_recv: Connection = cmd["work_recv"]
            result_send: Connection = cmd["result_send"]
            sdk_socket_path = cmd.get("sdk_socket_path")
            _handle_fork_request(
                pipe, worker_id, persistent, work_recv, result_send,
                sdk_socket_path, profile,
            )

    logger.info("Template process exiting")


def _load_workflow_runtime(pipe: Connection) -> dict[str, Any] | None:
    """Preload the execution engine and scrub platform credentials.

    Returns extra ``ready`` fields, or None after reporting a startup error.
    """
    # ----- Load heavy dependencies -----
    # These imports pull in the full transitive closure of each library.
    # After fork, children share these pages via COW.
    install_virtual_import_hook = None
    try:
        # Core bifrost SDK and execution engine
        try:
            import bifrost  # noqa: F401
        except ImportError:
            logger.warning("bifrost SDK not available — skipping preload")

        try:
            import httpx  # noqa: F401
        except ImportError:
            logger.warning("httpx not available — skipping preload")

        try:
            import pydantic  # noqa: F401
        except ImportError:
            logger.warning("pydantic not available — skipping preload")

        # Execution infrastructure
        try:
            from src.services.execution.virtual_import import install_virtual_import_hook
            # The supervisor installs packages before startup/recycle. Reuse
            # that filesystem without opening storage clients in this process.

            # Ensure user site-packages is in sys.path
            import site
            user_site = site.getusersitepackages()
            if site.ENABLE_USER_SITE and os.path.exists(user_site) and user_site not in sys.path:
                sys.path.insert(0, user_site)
                logger.info(f"Added user site-packages to sys.path: {user_site}")

            # Keep the hook callable loaded, but do not activate it until all
            # platform modules have been primed below. Once active, intentional
            # workspace/package-name collisions must be resolved in execution
            # scope, which the template does not have.
        except ImportError as e:
            logger.warning(f"Execution infrastructure not available: {e} — continuing without it")

    except Exception as e:
        logger.exception(f"Template process failed to load dependencies: {e}")
        pipe.send({"status": "error", "error": str(e)})
        pipe.close()
        return None

    # ----- Env scrub (Phase 2, M1) -----
    # Scrub credentials that the template loaded during startup but that
    # forked children must NOT inherit.  This point is chosen deliberately:
    # - Package installation already finished in the supervisor. This process
    #   does not need storage credentials or a second requirements fetch.
    # - BEFORE pipe.send({"status": "ready"}) — all forks inherit this scrubbed env.
    #
    # Assertion: get_settings() must NOT have been called by this point.
    # If it were, the lru_cache would hold a Settings object with the secrets;
    # we log loudly and the cache_clear() below evicts it before priming.
    from src.config import get_settings
    try:
        cache_info = get_settings.cache_info()
        if cache_info.currsize != 0:
            logger.error(
                "SECURITY: get_settings() cache is non-empty at env-scrub point "
                f"(currsize={cache_info.currsize}). A Settings object holding "
                "real secrets was constructed during template startup — find and "
                "remove that call. Evicting it now before the sanitized re-prime."
            )
        else:
            logger.info("get_settings cache_info at scrub: currsize=0 (clean)")
    except Exception as e:
        logger.warning(f"Could not check get_settings cache state: {e}")

    _SCRUB_KEYS = [
        "BIFROST_DATABASE_URL",
        "BIFROST_DATABASE_URL_SYNC",
        # Retired broker; an install upgraded with its old compose file or
        # manifests still sets this URL, credentials included.
        "BIFROST_RABBITMQ_URL",
        "BIFROST_REDIS_URL",
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_USERNAME",
        "REDIS_PASSWORD",
        "CACHE_REDIS_URL",
        # S3 credentials: child uses API endpoint fallback (Phase 2 step 2).
        "BIFROST_S3_ACCESS_KEY",
        "BIFROST_S3_SECRET_KEY",
        "BIFROST_S3_ENDPOINT_URL",
        "BIFROST_S3_BUCKET",
        "BIFROST_S3_REGION",
        # Execution children receive a freshly minted process-scoped SDK token
        # only after fork. Never let ambient API credentials cross the template.
        "BIFROST_ACCESS_TOKEN",
        "BIFROST_REFRESH_TOKEN",
    ]
    scrubbed = []
    for key in _SCRUB_KEYS:
        if key in os.environ:
            del os.environ[key]
            scrubbed.append(key)
    if scrubbed:
        logger.info(f"Scrubbed {len(scrubbed)} env var(s) from template before fork: {scrubbed}")
    else:
        logger.info("Env scrub: no forbidden vars found (already absent — expected in tests)")

    # BIFROST_SECRET_KEY needs more than deletion: Settings.secret_key is
    # required (min_length=32, no default), and child code calls get_settings()
    # on every execution (for example engine.py reads settings.public_url).
    # Deleting the var outright would crash Settings construction in every
    # fork; keeping it would hand every child the JWT-signing key. Prime the
    # cache with inert values, then delete them from the environment.
    # Forks inherit the cached object via COW: get_settings() keeps working
    # everywhere in the child, but its secret_key is the sentinel and its
    # DB/S3 fields are localhost defaults and Redis is an unreachable
    # endpoint. Real platform credentials exist in neither the child's env nor
    # its memory; tokens are pre-minted by the parent, so nothing in the child
    # signs JWTs.
    os.environ["BIFROST_SECRET_KEY"] = "scrubbed-execution-children-hold-no-platform-credentials"
    os.environ["BIFROST_REDIS_URL"] = "redis://127.0.0.1:0/0"
    get_settings.cache_clear()
    get_settings()
    del os.environ["BIFROST_SECRET_KEY"]
    del os.environ["BIFROST_REDIS_URL"]
    logger.info("settings cache primed with inert secret and Redis values, env vars removed")

    # Preload the platform execution runtime after credential scrubbing. Every
    # execution runs in a fresh one-shot fork; leaving this import to the child
    # makes each request pay the full SDK + engine import cost before the engine
    # duration timer even starts. Forked children inherit these modules through
    # copy-on-write, so the pod pays that cost once while workflow code itself
    # remains freshly loaded per execution.
    # SDK calls run under execution tracing; rebuilding models/client code in
    # every child adds avoidable latency. Share these common runtime modules,
    # while leaving database and object-storage implementations out entirely.
    import bifrost.client  # noqa: F401
    import bifrost.models  # noqa: F401
    import src.sdk.decorators  # noqa: F401
    import bifrost.credentials  # noqa: F401
    import src.models.enums  # noqa: F401
    import src.sdk.context  # noqa: F401
    import src.services.execution.engine  # noqa: F401
    import src.services.execution.worker  # noqa: F401

    # Platform priming must finish before the virtual finder is activated.
    # Forked children inherit the hook and provide the execution-scoped module
    # resolver needed for workspace and Solution imports.
    if install_virtual_import_hook is not None:
        install_virtual_import_hook()

    return {}


def _load_agent_runtime(pipe: Connection) -> dict[str, Any] | None:
    """Preload the agent runtime with the platform credentials it needs.

    The agent consumer opens database sessions and decrypts the model
    provider's key, so this profile keeps the environment and settings the
    workflow profile scrubs. Imports and mapper configuration only: no event
    loop, thread or connection exists when children fork. The API
    application (``src.main``) is not loaded; children reach REST over HTTP
    (see ``src.jobs.agent_run_child``).

    Returns extra ``ready`` fields, or None after reporting a startup error.
    """
    try:
        import asyncpg  # noqa: F401
        import pydantic_ai.models.openai  # noqa: F401
        import pydantic_ai.providers.openai  # noqa: F401
        import sqlalchemy.dialects.postgresql.asyncpg  # noqa: F401
        from src.jobs.agent_run_child import run_agent_message  # noqa: F401
        import src.jobs.consumers.agent_run  # noqa: F401
        import src.services.agent_executor  # noqa: F401
        import src.services.agent_runtime.tool_policy  # noqa: F401
        from genai_prices.data_snapshot import get_snapshot
        from sqlalchemy.orm import configure_mappers

        # SQLAlchemy configures mappers on first use, rewriting every mapper
        # object. Doing it once here keeps those pages shared instead of
        # copying them into each child's first query.
        configure_mappers()
        # Every run prices its usage and resolves its model from the catalog.
        # Parsing either per child cost most of a run's CPU and memory; parsed
        # here, children share them and the catalog's own freshness check
        # (``get_model_catalog``) still reloads a changed catalog.
        get_snapshot()
        asyncio.run(_warm_model_catalog())
    except Exception as e:
        logger.exception(f"Agent template failed to load dependencies: {e}")
        pipe.send({"status": "error", "error": str(e)})
        pipe.close()
        return None
    return {}


async def _warm_model_catalog() -> None:
    """Parse the model catalog once, leaving no connection open for forks."""
    from src.core.database import close_db, get_db_context, init_db
    from src.services.model_catalog import get_model_catalog

    await init_db()
    try:
        async with get_db_context() as db:
            await get_model_catalog(db)
    finally:
        await close_db()


def _template_subprocess_entry(control_fd: int, profile: TemplateProfile) -> None:
    """
    Minimal subprocess entrypoint for the template.

    The parent sends the multiprocessing authkey as the first frame on the
    private inherited control Connection. That happens before any Connection
    descriptors are received, preserving multiprocessing.resource_sharer
    authentication without putting the authkey in argv or the environment.
    """
    multiprocessing.set_start_method("spawn", force=True)
    multiprocessing.current_process().name = "template-process"
    pipe = Connection(control_fd)
    authkey = pipe.recv_bytes(maxlength=256)
    if not authkey:
        raise RuntimeError("template subprocess received an empty authkey")
    multiprocessing.current_process().authkey = authkey
    _template_main(pipe, profile)


def _handle_fork_request(
    pipe: Connection,
    worker_id: str,
    persistent: bool,
    work_recv: Connection,
    result_send: Connection,
    sdk_socket_path: str | None = None,
    profile: TemplateProfile = "workflow",
) -> None:
    """
    Handle a fork request: fork and wire up pre-created pipe connections.

    The consumer creates two Pipe() pairs before calling fork():
      - work pipe:   consumer writes via work_send; child reads via work_recv
      - result pipe: child writes via result_send; consumer reads via result_recv

    The consumer sends work_recv and result_send to us (picklable Connections).
    We fork, the child inherits them, we close them in the parent and
    reply with just the child_pid.

    Engine SDK access does not travel on an inherited channel: the worker
    parent serves the ordinary SDK HTTP routes on a private Unix socket and
    passes its path through ``sdk_socket_path``. The work/result pipes remain
    the only descriptors handed to the child at fork.

    Args:
        pipe: Control pipe to send response back to consumer.
        worker_id: ID to assign to the forked child worker.
        persistent: If True, child loops for multiple executions.
                    If False, child runs one execution and exits.
        work_recv: Read end of work pipe (child reads execution IDs from here).
        result_send: Write end of result pipe (child writes results here).
        sdk_socket_path: Worker-local Unix socket the child should use for
            engine SDK HTTP calls, or None when no socket is served.
        profile: The template's profile; selects the child's entry.
    """
    if profile == "workflow":
        # The virtual import hook preloads a parent-side HTTP client. Clear
        # that and any sync Redis source-cache client immediately before fork
        # so the child reconstructs only its injected worker-UDS transport.
        _clear_pre_fork_source_clients()
    child_pid = os.fork()

    if child_pid > 0:
        # ----- Parent (template) -----
        # Close the child-side connections — the child owns them now
        for conn in (work_recv, result_send):
            try:
                conn.close()
            except (OSError, BrokenPipeError) as e:
                # Already closed — ignore
                logger.debug(f"parent: work/result conn.close ignored: {e}")

        # Send child PID back to consumer
        pipe.send({
            "status": "forked",
            "child_pid": child_pid,
            "worker_id": worker_id,
        })
    else:
        # ----- Child -----
        from src.services.execution.oom_priority import volunteer_for_oom_kill

        volunteer_for_oom_kill()
        # Close the template's control pipe — child doesn't need it
        try:
            pipe.close()
        except (OSError, BrokenPipeError) as e:
            # Already closed in parent post-fork — ignore
            logger.debug(f"child: pipe.close ignored: {e}")

        # Run the worker function (this blocks until the child exits)
        if profile == "agent":
            # Never unwind into the template's fork loop or its exit handlers.
            exit_code = 1
            try:
                _run_agent_child(work_recv, result_send, worker_id)
                exit_code = 0
            finally:
                os._exit(exit_code)
        else:
            _run_forked_child(
                work_recv, result_send, worker_id, persistent,
                sdk_socket_path=sdk_socket_path,
            )
        os._exit(0)


def _run_forked_child(
    work_recv: Connection,
    result_send: Connection,
    worker_id: str,
    persistent: bool,
    sdk_socket_path: str | None = None,
) -> None:
    """
    Entry point for a forked child process.

    The child inherits all loaded modules from the template via COW and creates
    its own event loop. Its execution context arrives on the private work pipe.
    Engine SDK access uses ordinary HTTP over the worker-local Unix socket the
    parent injects as ``sdk_socket_path``; the child never receives DB, S3, or
    provider credentials.

    Communication uses raw Connection objects (Pipe ends) that were
    inherited via fork — no pickling required.

    When the parent serves a socket (``sdk_socket_path`` is set), the
    engine socket transport is installed before user code runs and cleared
    afterwards. This is the explicit injection point: there is no
    user-controlled flag, and outside this path the SDK uses the network HTTP
    API.

    Args:
        work_recv: Read end of work pipe; receives ``(execution_id, context)``.
        result_send: Write end of result pipe; sends result dicts via .send().
        worker_id: Identifier for logging.
        persistent: If True, loop for multiple executions. If False, run once.
        sdk_socket_path: Worker-local Unix socket for engine SDK HTTP calls,
            injected only by the worker parent. When present the child sends
            the ordinary HTTP request over it instead of the network API.
    """
    # Reconfigure logging for this child
    logging.basicConfig(
        level=logging.INFO,
        format=f"[{worker_id}] %(levelname)s - %(message)s",
        force=True,
    )

    # Mark this post-fork child before any user code runs. The scoped SDK
    # transport uses the worker-local socket when one is injected, otherwise
    # it uses the ordinary network engine client.
    from src.core.module_cache_sync import mark_execution_child

    mark_execution_child()

    # Engine start: install the worker-local SDK socket transport before any
    # user code runs.
    from bifrost.client import _clear_engine_socket, _install_engine_socket

    if sdk_socket_path is not None:
        _install_engine_socket(sdk_socket_path)
        logger.info(f"Forked worker {worker_id} using engine-local SDK socket")

    # Setup signal handler for graceful shutdown
    shutdown_requested = False

    def handle_sigterm(signum: int, frame: Any) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.info(f"Worker {worker_id} received SIGTERM, will exit after current work")

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    logger.info(f"Forked worker {worker_id} started (PID={os.getpid()}, persistent={persistent})")

    execution_id: str | None = None

    while not shutdown_requested:
        try:
            # Block waiting for work (poll with timeout to check shutdown flag)
            if not work_recv.poll(timeout=1.0):
                continue

            work_item = work_recv.recv()

            execution_id, context = work_item

            logger.info(f"Worker {worker_id} processing execution: {execution_id[:8]}...")

            # NOTE: workspace-module eviction is done INSIDE _execute_sync /
            # _execute_async, AFTER the execution's Solution context is read from
            # Redis and activated. Clearing here (before the context exists) ran
            # the cross-solution eviction blind and could let a prior install's
            # same-name module survive, breaking multi-install isolation (Codex #9).

            # Execute
            try:
                from src.services.execution.simple_worker import _execute_sync
                result = _execute_sync(execution_id, worker_id, context)
            except ImportError:
                result = {
                    "execution_id": execution_id,
                    "success": False,
                    "error": "Execution infrastructure not available",
                    "error_type": "ImportError",
                    "duration_ms": 0,
                    "worker_id": worker_id,
                }

            # Clean up per-execution state
            try:
                from bifrost._logging import clear_sequence_counter
                clear_sequence_counter(execution_id)
            except Exception as e:
                # bifrost._logging may not be importable; counter cleanup is best-effort
                logger.debug(f"clear_sequence_counter failed for {execution_id}: {e}")

            # Report current RSS without a full collection. This child is
            # one-shot and exits immediately after sending the result, so a
            # stop-the-world collection adds response latency but cannot
            # reclaim memory for reuse.
            try:
                from src.services.execution.simple_worker import _get_process_rss
                process_rss = _get_process_rss()
                result["process_rss_bytes"] = process_rss
                if isinstance(result.get("metrics"), dict):
                    result["metrics"]["process_rss_bytes"] = process_rss
            except (ImportError, OSError, KeyError) as e:
                # simple_worker import optional; _get_process_rss reads /proc which may
                # be missing on macOS; result may be a non-dict — all best-effort metrics
                logger.debug(f"could not record RSS for execution {execution_id}: {e}")

            result_send.send(result)

            logger.info(
                f"Worker {worker_id} completed execution: {execution_id[:8]}... "
                f"success={result.get('success', False)}"
            )

            execution_id = None

            # On-demand mode: exit after one execution
            if not persistent:
                break

        except (EOFError, OSError):
            # Work pipe closed — consumer disconnected
            logger.info(f"Worker {worker_id} work pipe closed, exiting")
            break
        except KeyboardInterrupt:
            logger.info(f"Worker {worker_id} interrupted")
            break
        except Exception as e:
            logger.exception(f"Worker {worker_id} error: {e}")
            if execution_id is not None:
                try:
                    result_send.send({
                        "execution_id": execution_id,
                        "success": False,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "duration_ms": 0,
                        "worker_id": worker_id,
                    })
                except (OSError, BrokenPipeError) as send_err:
                    # Result pipe closed (consumer gone) — child is about to exit anyway
                    logger.debug(f"could not send error result for {execution_id}: {send_err}")
                execution_id = None

            # On-demand mode: exit even on error
            if not persistent:
                break

    # Engine teardown: drop the engine socket so a reused (persistent)
    # child never serves the next execution on a stale socket.
    _clear_engine_socket()

    logger.info(f"Worker {worker_id} exiting")


def _run_agent_child(
    work_recv: Connection,
    result_send: Connection,
    worker_id: str,
) -> None:
    """Run one ``agent.run`` dispatch in a child forked from the agent template.

    Reads one work item ``{"message": dict}``, runs the agent consumer with
    connections opened in this child, and replies ``{"ok": True}`` or
    ``{"ok": False, "error_type": ..., "error": ...}``. A child that dies
    without replying is reported as a crash by its supervisor.
    """
    logging.basicConfig(
        level=logging.INFO,
        format=f"[{worker_id}] %(levelname)s - %(message)s",
        force=True,
    )
    try:
        work_item = work_recv.recv()
    except (EOFError, OSError):
        logger.info(f"Agent child {worker_id} work pipe closed before a run arrived")
        return

    from src.jobs.agent_run_child import run_agent_message

    try:
        asyncio.run(run_agent_message(work_item["message"]))
        reply: dict[str, Any] = {"ok": True}
    # A run that exits or is cancelled still reports how it ended.
    except (Exception, SystemExit, asyncio.CancelledError) as e:
        logger.exception(f"Agent child {worker_id} run failed: {e}")
        reply = {"ok": False, "error_type": type(e).__name__, "error": str(e)}
    result_send.send(reply)


class TemplateProcess:
    """
    Manages the lifecycle of the template process.

    The template process is a long-lived, single-threaded process that
    holds all heavy dependencies in memory and forks children on request.
    ``profile`` selects what it preloads and what its children run (see the
    module docstring).
    """

    def __init__(self, profile: TemplateProfile = "workflow") -> None:
        self.profile: TemplateProfile = profile
        self._process: subprocess.Popen[bytes] | None = None
        self._pipe: Connection | None = None
        self.pid: int | None = None
        self.start_method: str | None = None
        self.process_name: str | None = None
        self.frozen_objects: int | None = None

    def start(self) -> None:
        """
        Spawn the template process and wait for it to be ready.

        Blocks until the template has loaded all dependencies and
        signaled ready, or raises if startup fails.
        """
        if self.is_alive():
            return  # Already running
        if self._process is not None:
            self._process.wait(timeout=0)
            self._process = None
            self.pid = None
            self.start_method = None
            self.process_name = None
            self.frozen_objects = None
            if self._pipe is not None:
                with suppress(OSError, BrokenPipeError):
                    self._pipe.close()
                self._pipe = None

        parent_conn, child_conn = multiprocessing.Pipe()
        authkey = bytes(multiprocessing.current_process().authkey)
        argv = [
            sys.executable,
            "-m",
            "src.services.execution.template_process",
            str(child_conn.fileno()),
            self.profile,
        ]

        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                argv,
                pass_fds=(child_conn.fileno(),),
                close_fds=True,
            )
            child_conn.close()
            parent_conn.send_bytes(authkey)

            self._process = process
            self._pipe = parent_conn

            # Wait for ready signal (with timeout)
            if not parent_conn.poll(timeout=TEMPLATE_START_TIMEOUT_SECONDS):
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError(
                    f"Template process failed to start within {TEMPLATE_START_TIMEOUT_SECONDS} seconds"
                )

            msg = parent_conn.recv()
            if msg.get("status") == "error":
                raise RuntimeError(f"Template process startup failed: {msg.get('error')}")

            self.pid = msg.get("pid", process.pid)
            self.start_method = msg.get("start_method")
            self.process_name = msg.get("process_name")
            self.frozen_objects = msg.get("frozen_objects")
            logger.info(f"Template process ready (PID={self.pid}, profile={self.profile})")
        except Exception:
            with suppress(OSError, BrokenPipeError):
                child_conn.close()
            with suppress(OSError, BrokenPipeError):
                parent_conn.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            self._process = None
            self._pipe = None
            self.pid = None
            self.start_method = None
            self.process_name = None
            self.frozen_objects = None
            raise

    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
        *,
        sdk_socket_path: str | None = None,
    ) -> tuple[int, _SendQueue, _RecvQueue]:
        """
        Request the template to fork a new child worker.

        Creates two Pipe pairs before sending the fork command. The
        pipe connections are picklable and are sent to the template
        process, which passes them to the forked child via fork
        inheritance. The consumer keeps the parent-side ends.

        Engine SDK access does not travel on a dedicated channel. When the
        worker serves one, the child receives ``sdk_socket_path`` and makes
        ordinary HTTP requests over that Unix socket instead of the network
        API.

        Args:
            worker_id: Identifier for the new worker (for logging).
            persistent: If True, child loops for multiple executions.
                        If False (default), child runs one execution and exits.
            sdk_socket_path: Worker-local Unix socket for engine SDK HTTP
                calls, forwarded to the child at fork. None when the worker
                serves no socket.

        Returns:
            ``(child_pid, work_queue, result_queue)``.
            work_queue.put(execution_id) sends work to the child.
            result_queue.get() retrieves the result from the child.

        Raises:
            RuntimeError: If template is not running.
        """
        if self._pipe is None or not self.is_alive():
            raise RuntimeError("Template process is not running")

        # Create pipe pairs:
        #   work:   consumer writes (work_send) → child reads (work_recv)
        #   result: child writes (result_send) → consumer reads (result_recv)
        work_recv, work_send = multiprocessing.Pipe(duplex=False)
        result_recv, result_send = multiprocessing.Pipe(duplex=False)

        try:
            # Send fork command with child-side connections (picklable)
            self._pipe.send({
                "action": CMD_FORK,
                "worker_id": worker_id,
                "persistent": persistent,
                "work_recv": work_recv,
                "result_send": result_send,
                "sdk_socket_path": sdk_socket_path,
            })

            # Close child-side connections on our end after sending
            work_recv.close()
            result_send.close()

            # Wait for fork response (just child_pid)
            if not self._pipe.poll(timeout=30):
                raise RuntimeError("Template process did not respond to fork request within 30s")

            msg = self._pipe.recv()
            if msg.get("status") != "forked":
                raise RuntimeError(f"Unexpected fork response: {msg}")
        except Exception:
            for conn in (work_recv, work_send, result_recv, result_send):
                with suppress(OSError, BrokenPipeError):
                    conn.close()
            raise

        # Return queue-like wrappers around the consumer-side pipe ends.
        # work_queue:   consumer calls .put(execution_id) → sends via work_send
        # result_queue: consumer calls .get() → reads via result_recv
        work_queue = _SendQueue(work_send)
        result_queue = _RecvQueue(result_recv)

        return (
            msg["child_pid"],
            work_queue,
            result_queue,
        )

    def collect_child_exit_statuses(self) -> dict[int, int]:
        """Return statuses for children reaped by the template since the last call."""
        if self._pipe is None or not self.is_alive():
            return {}

        self._pipe.send({"action": CMD_GET_EXIT_STATUSES})
        if not self._pipe.poll(timeout=5):
            raise RuntimeError("Template process did not return child exit statuses within 5s")

        msg = self._pipe.recv()
        if msg.get("status") != "exit_statuses":
            raise RuntimeError(f"Unexpected template exit-status response: {msg}")

        return {
            int(child_pid): int(exit_code)
            for child_pid, exit_code in msg.get("exit_statuses", [])
        }

    def shutdown(self) -> None:
        """Send shutdown command to template and wait for it to exit."""
        if self._pipe is not None:
            try:
                self._pipe.send({"action": CMD_SHUTDOWN})
            except (OSError, BrokenPipeError) as e:
                # Template already exited — no need to send shutdown
                logger.debug(f"template pipe closed before shutdown send: {e}")

        if self._process is not None:
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

        if self._pipe is not None:
            try:
                self._pipe.close()
            except (OSError, BrokenPipeError) as e:
                logger.debug(f"template pipe close during shutdown ignored: {e}")

        self._pipe = None
        self._process = None
        self.pid = None
        self.start_method = None
        self.process_name = None
        self.frozen_objects = None

    def is_alive(self) -> bool:
        """Check if the template process is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in get_args(TemplateProfile):
        raise SystemExit(
            "usage: python -m src.services.execution.template_process <control_fd> <workflow|agent>"
        )
    _template_subprocess_entry(int(sys.argv[1]), sys.argv[2])  # pyright: ignore[reportArgumentType]
