"""
Template Process for Fork-Based Worker Pool.

A single-threaded process that pre-loads all heavy dependencies and forks
children on request. Children share the template's memory pages via
copy-on-write (COW), drastically reducing per-worker memory overhead.

The template process NEVER:
- Starts an asyncio event loop
- Opens Redis/DB/RabbitMQ connections
- Spawns background threads
- Initializes thread-based logging handlers

This ensures clean fork behavior (no inherited locked mutexes).
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import subprocess
import sys
from contextlib import suppress
from multiprocessing.connection import Connection
from typing import Any, Literal, overload

logger = logging.getLogger(__name__)


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

# Commands sent from consumer to template via pipe
CMD_FORK = "fork"
CMD_GET_EXIT_STATUSES = "get_exit_statuses"
CMD_SHUTDOWN = "shutdown"


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
) -> None:
    """
    Entry point for the template process.

    Loads all heavy dependencies, installs import hooks, then waits for fork
    commands on the pipe. This function runs in the template subprocess after
    the entrypoint restores multiprocessing spawn semantics.

    Args:
        pipe: Connection to receive commands from and send responses to consumer.
    """
    # Configure logging (no thread-based handlers)
    logging.basicConfig(
        level=logging.INFO,
        format="[template] %(levelname)s - %(message)s",
    )

    logger.info(f"Template process starting (PID={os.getpid()})")

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

        try:
            import redis  # noqa: F401
        except ImportError:
            logger.warning("redis not available — skipping preload")

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
        return

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
        "BIFROST_RABBITMQ_URL",
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
    # on every execution (engine.py reads settings.public_url; the SDK redis
    # helpers read settings.redis_url). Deleting the var outright would crash
    # Settings construction in every fork; keeping it would hand every child
    # the JWT-signing key. So: swap in an inert sentinel, prime the
    # get_settings() cache from the now-scrubbed env, then delete the var.
    # Forks inherit the cached object via COW: get_settings() keeps working
    # everywhere in the child, but its secret_key is the sentinel and its
    # DB/RabbitMQ/S3 fields are localhost defaults. The real key exists in
    # neither the child's env nor its memory; tokens are pre-minted by the
    # parent (token hand-down), so nothing in the child signs JWTs.
    os.environ["BIFROST_SECRET_KEY"] = "scrubbed-execution-children-hold-no-platform-credentials"
    get_settings.cache_clear()
    get_settings()
    del os.environ["BIFROST_SECRET_KEY"]
    logger.info("SECRET_KEY scrubbed: settings cache primed with inert sentinel, env var removed")

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
    import bifrost._local_transport  # noqa: F401  # stdlib-only; installed per-fork, never auto-selected
    import bifrost._import_transport  # noqa: F401  # stdlib-only sync import channel; installed per-fork
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

    logger.info("Template process ready — all dependencies loaded")
    pipe.send({
        "status": "ready",
        "pid": os.getpid(),
        "start_method": multiprocessing.get_start_method(),
        "process_name": multiprocessing.current_process().name,
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
            with_sdk = bool(cmd.get("with_sdk", False))
            with_import = bool(cmd.get("with_import", False))
            _handle_fork_request(
                pipe, worker_id, persistent, work_recv, result_send,
                with_sdk, with_import,
            )

    logger.info("Template process exiting")


def _template_subprocess_entry(control_fd: int) -> None:
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
    _template_main(pipe)


def _handle_fork_request(
    pipe: Connection,
    worker_id: str,
    persistent: bool,
    work_recv: Connection,
    result_send: Connection,
    with_sdk: bool = False,
    with_import: bool = False,
) -> None:
    """
    Handle a fork request: fork and wire up pre-created pipe connections.

    The consumer creates two Pipe() pairs before calling fork():
      - work pipe:   consumer writes via work_send; child reads via work_recv
      - result pipe: child writes via result_send; consumer reads via result_recv

    The consumer sends work_recv and result_send to us (picklable Connections).
    We fork, the child inherits them, we close them in the parent and
    reply with just the child_pid.

    When ``with_sdk`` is set, the template additionally creates a dedicated
    SDK channel pair here (child writes requests on one pipe, reads
    responses on the other) and returns the parent ends to the consumer in
    the fork reply. The SDK pipes are created in the template — not the
    consumer — because raw fork inheritance is the only way the child
    acquires them; the control-pipe fd passing carries the parent ends back.
    These channels are separate from the work/result pipes.

    When ``with_import`` is set, the template additionally creates a second
    dedicated import channel pair for synchronous module resolution and
    source fetch. It is separate from the async SDK channel because an
    import may block the child's event loop while an async SDK request is
    awaiting a response — sharing that channel's lock could deadlock.
    The child installs the stdlib-only synchronous import transport on its
    ends before user code runs.

    Args:
        pipe: Control pipe to send response back to consumer.
        worker_id: ID to assign to the forked child worker.
        persistent: If True, child loops for multiple executions.
                    If False, child runs one execution and exits.
        work_recv: Read end of work pipe (child reads execution IDs from here).
        result_send: Write end of result pipe (child writes results here).
        with_sdk: If True, create a dedicated local-SDK channel for the child.
        with_import: If True, create a dedicated local import channel.
    """
    sdk_req_recv: Connection | None = None
    sdk_req_send: Connection | None = None
    sdk_resp_recv: Connection | None = None
    sdk_resp_send: Connection | None = None
    imp_req_recv: Connection | None = None
    imp_req_send: Connection | None = None
    imp_resp_recv: Connection | None = None
    imp_resp_send: Connection | None = None
    if with_sdk:
        # Request pipe: child writes (sdk_req_send), consumer reads
        # (sdk_req_recv). Response pipe: consumer writes (sdk_resp_send),
        # child reads (sdk_resp_recv).
        sdk_req_recv, sdk_req_send = multiprocessing.Pipe(duplex=False)
        sdk_resp_recv, sdk_resp_send = multiprocessing.Pipe(duplex=False)
    if with_import:
        # Import request pipe: child writes (imp_req_send), consumer reads
        # (imp_req_recv). Import response pipe: consumer writes
        # (imp_resp_send), child reads (imp_resp_recv).
        imp_req_recv, imp_req_send = multiprocessing.Pipe(duplex=False)
        imp_resp_recv, imp_resp_send = multiprocessing.Pipe(duplex=False)

    child_pid = os.fork()

    if child_pid > 0:
        # ----- Parent (template) -----
        # Close the child-side connections — the child owns them now
        for conn in (work_recv, result_send, sdk_req_send, sdk_resp_recv,
                     imp_req_send, imp_resp_recv):
            if conn is None:
                continue
            try:
                conn.close()
            except (OSError, BrokenPipeError) as e:
                # Already closed — ignore
                logger.debug(f"parent: sdk/work conn.close ignored: {e}")

        # Send child PID back to consumer (parent SDK/import ends travel via
        # fd passing; our copies close right after the send)
        reply: dict[str, Any] = {
            "status": "forked",
            "child_pid": child_pid,
            "worker_id": worker_id,
        }
        if with_sdk:
            assert sdk_req_recv is not None and sdk_resp_send is not None
            reply["sdk_req_recv"] = sdk_req_recv
            reply["sdk_resp_send"] = sdk_resp_send
        if with_import:
            assert imp_req_recv is not None and imp_resp_send is not None
            reply["imp_req_recv"] = imp_req_recv
            reply["imp_resp_send"] = imp_resp_send
        pipe.send(reply)
        for conn in (sdk_req_recv, sdk_resp_send, imp_req_recv, imp_resp_send):
            if conn is None:
                continue
            try:
                conn.close()
            except (OSError, BrokenPipeError) as e:
                logger.debug(f"parent: sdk parent-end close ignored: {e}")
    else:
        # ----- Child -----
        # Close the template's control pipe — child doesn't need it
        try:
            pipe.close()
        except (OSError, BrokenPipeError) as e:
            # Already closed in parent post-fork — ignore
            logger.debug(f"child: pipe.close ignored: {e}")
        # Close the parent-side SDK/import ends — the consumer owns them
        for conn in (sdk_req_recv, sdk_resp_send, imp_req_recv, imp_resp_send):
            if conn is None:
                continue
            try:
                conn.close()
            except (OSError, BrokenPipeError) as e:
                logger.debug(f"child: sdk parent-end close ignored: {e}")

        # Run the worker function (this blocks until the child exits)
        _run_forked_child(
            work_recv, result_send, worker_id, persistent,
            sdk_req_send=sdk_req_send, sdk_resp_recv=sdk_resp_recv,
            imp_req_send=imp_req_send, imp_resp_recv=imp_resp_recv,
        )
        os._exit(0)


def _run_forked_child(
    work_recv: Connection,
    result_send: Connection,
    worker_id: str,
    persistent: bool,
    sdk_req_send: Connection | None = None,
    sdk_resp_recv: Connection | None = None,
    imp_req_send: Connection | None = None,
    imp_resp_recv: Connection | None = None,
) -> None:
    """
    Entry point for a forked child process.

    The child inherits all loaded modules from the template via COW and creates
    its own event loop. Its execution context arrives on the private work pipe;
    SDK/module access may still use network clients during the workflow.

    Communication uses raw Connection objects (Pipe ends) that were
    inherited via fork — no pickling required.

    When the template created a dedicated SDK channel (``sdk_req_send`` /
    ``sdk_resp_recv``), the engine-selected local SDK transport is installed
    before user code runs and cleared afterwards. This is the explicit
    injection point: there is no user-controlled flag, and outside this
    path no transport exists so the SDK uses HTTP.

    When the template created a dedicated import channel (``imp_req_send`` /
    ``imp_resp_recv``), the stdlib-only synchronous import transport is
    installed alongside it — before user code runs and before the entry
    workflow's own source is loaded — so cold module resolution and source
    fetch ride the parent instead of HTTP/S3. It is a separate pipe pair
    because an import may block the child's event loop while an async SDK
    request is awaiting a response.

    Args:
        work_recv: Read end of work pipe; receives ``(execution_id, context)``.
        result_send: Write end of result pipe; sends result dicts via .send().
        worker_id: Identifier for logging.
        persistent: If True, loop for multiple executions. If False, run once.
        sdk_req_send: Write end of the SDK request pipe (child → parent).
        sdk_resp_recv: Read end of the SDK response pipe (parent → child).
        imp_req_send: Write end of the import request pipe (child → parent).
        imp_resp_recv: Read end of the import response pipe (parent → child).
    """
    # Reconfigure logging for this child
    logging.basicConfig(
        level=logging.INFO,
        format=f"[{worker_id}] %(levelname)s - %(message)s",
        force=True,
    )

    # Engine start: select the local SDK transport when the template wired
    # a dedicated channel. Installed before any user code runs.
    from bifrost._local_transport import clear as _clear_local_transport
    from bifrost._local_transport import install as _install_local_transport
    from bifrost._import_transport import clear as _clear_import_transport
    from bifrost._import_transport import install as _install_import_transport

    if sdk_req_send is not None and sdk_resp_recv is not None:
        _install_local_transport(sdk_req_send, sdk_resp_recv)
        logger.info(f"Forked worker {worker_id} using engine-local SDK transport")
    if imp_req_send is not None and imp_resp_recv is not None:
        _install_import_transport(imp_req_send, imp_resp_recv)
        logger.info(f"Forked worker {worker_id} using engine-local import transport")

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

    # Engine teardown: drop the local transports so a reused (persistent)
    # child never serves the next execution on a stale channel, and release
    # the child-side descriptors.
    _clear_local_transport()
    _clear_import_transport()
    for _conn in (sdk_req_send, sdk_resp_recv, imp_req_send, imp_resp_recv):
        if _conn is None:
            continue
        try:
            _conn.close()
        except (OSError, BrokenPipeError) as _e:
            logger.debug(f"child: sdk close ignored: {_e}")

    logger.info(f"Worker {worker_id} exiting")


class TemplateProcess:
    """
    Manages the lifecycle of the template process.

    The template process is a long-lived, single-threaded process that
    holds all heavy dependencies in memory and forks children on request.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._pipe: Connection | None = None
        self.pid: int | None = None
        self.start_method: str | None = None
        self.process_name: str | None = None

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
            if not parent_conn.poll(timeout=120):
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("Template process failed to start within 120 seconds")

            msg = parent_conn.recv()
            if msg.get("status") == "error":
                raise RuntimeError(f"Template process startup failed: {msg.get('error')}")

            self.pid = msg.get("pid", process.pid)
            self.start_method = msg.get("start_method")
            self.process_name = msg.get("process_name")
            logger.info(f"Template process ready (PID={self.pid})")
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
            raise

    @overload
    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
        with_sdk: Literal[False] = False,
        with_import: Literal[False] = False,
    ) -> tuple[int, _SendQueue, _RecvQueue]:
        ...

    @overload
    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
        with_sdk: Literal[True] = True,
        with_import: Literal[False] = False,
    ) -> tuple[int, _SendQueue, _RecvQueue, Any, Any]:
        ...

    @overload
    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
        with_sdk: Literal[False] = False,
        with_import: Literal[True] = True,
    ) -> tuple[int, _SendQueue, _RecvQueue, Any, Any]:
        ...

    @overload
    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
        with_sdk: bool = True,
        with_import: bool = True,
    ) -> tuple[int, _SendQueue, _RecvQueue, Any, Any, Any, Any]:
        ...

    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
        with_sdk: bool = False,
        with_import: bool = False,
    ) -> (
        tuple[int, _SendQueue, _RecvQueue]
        | tuple[int, _SendQueue, _RecvQueue, Any, Any]
        | tuple[int, _SendQueue, _RecvQueue, Any, Any, Any, Any]
    ):
        """
        Request the template to fork a new child worker.

        Creates two Pipe pairs before sending the fork command. The
        pipe connections are picklable and are sent to the template
        process, which passes them to the forked child via fork
        inheritance. The consumer keeps the parent-side ends.

        When ``with_sdk`` is set, the template additionally creates a
        dedicated local-SDK channel pair at fork time and returns its
        parent ends (request-recv, response-send) appended to the tuple.
        The child installs the engine-local SDK transport on its ends
        before user code runs.

        When ``with_import`` is set, the template additionally creates a
        second dedicated import channel pair (parent ends appended after
        the SDK ends) for synchronous module resolution and source fetch.
        The child installs the stdlib-only synchronous import transport on
        its ends before user code runs. It is separate from the SDK pair
        because an import may block the child's event loop while an async
        SDK request is awaiting a response.

        Args:
            worker_id: Identifier for the new worker (for logging).
            persistent: If True, child loops for multiple executions.
                        If False (default), child runs one execution and exits.
            with_sdk: If True, also wire a dedicated local-SDK channel.
            with_import: If True, also wire a dedicated local import channel.

        Returns:
            ``(child_pid, work_queue, result_queue)``, or with ``with_sdk``
            ``(child_pid, work_queue, result_queue, sdk_req_recv,
            sdk_resp_send)``, or with both ``(child_pid, work_queue,
            result_queue, sdk_req_recv, sdk_resp_send, imp_req_recv,
            imp_resp_send)``.
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

        sdk_req_recv: Any = None
        sdk_resp_send: Any = None
        imp_req_recv: Any = None
        imp_resp_send: Any = None
        try:
            # Send fork command with child-side connections (picklable)
            self._pipe.send({
                "action": CMD_FORK,
                "worker_id": worker_id,
                "persistent": persistent,
                "work_recv": work_recv,
                "result_send": result_send,
                "with_sdk": with_sdk,
                "with_import": with_import,
            })

            # Close child-side connections on our end after sending
            work_recv.close()
            result_send.close()

            # Wait for fork response (just child_pid now)
            if not self._pipe.poll(timeout=30):
                raise RuntimeError("Template process did not respond to fork request within 30s")

            msg = self._pipe.recv()
            if msg.get("status") != "forked":
                raise RuntimeError(f"Unexpected fork response: {msg}")
            if with_sdk:
                sdk_req_recv = msg.get("sdk_req_recv")
                sdk_resp_send = msg.get("sdk_resp_send")
                if sdk_req_recv is None or sdk_resp_send is None:
                    raise RuntimeError(f"Fork response missing SDK channel: {msg}")
            if with_import:
                imp_req_recv = msg.get("imp_req_recv")
                imp_resp_send = msg.get("imp_resp_send")
                if imp_req_recv is None or imp_resp_send is None:
                    raise RuntimeError(f"Fork response missing import channel: {msg}")
        except Exception:
            for conn in (work_recv, work_send, result_recv, result_send,
                         sdk_req_recv, sdk_resp_send,
                         imp_req_recv, imp_resp_send):
                if conn is None:
                    continue
                with suppress(OSError, BrokenPipeError):
                    conn.close()
            raise

        # Return queue-like wrappers around the consumer-side pipe ends.
        # work_queue:   consumer calls .put(execution_id) → sends via work_send
        # result_queue: consumer calls .get() → reads via result_recv
        work_queue = _SendQueue(work_send)
        result_queue = _RecvQueue(result_recv)

        if with_sdk and with_import:
            return (
                msg["child_pid"],
                work_queue,
                result_queue,
                sdk_req_recv,
                sdk_resp_send,
                imp_req_recv,
                imp_resp_send,
            )
        if with_sdk:
            return (
                msg["child_pid"],
                work_queue,
                result_queue,
                sdk_req_recv,
                sdk_resp_send,
            )
        if with_import:
            return (
                msg["child_pid"],
                work_queue,
                result_queue,
                imp_req_recv,
                imp_resp_send,
            )
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

    def is_alive(self) -> bool:
        """Check if the template process is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m src.services.execution.template_process <control_fd>")
    _template_subprocess_entry(int(sys.argv[1]))
