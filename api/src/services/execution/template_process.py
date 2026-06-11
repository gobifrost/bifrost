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
import sys
from multiprocessing.connection import Connection
from typing import Any

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

    def close(self) -> None:
        try:
            self._conn.close()
        except (OSError, BrokenPipeError) as e:
            # Connection already closed or broken — close is idempotent
            logger.debug(f"_RecvQueue.close ignoring: {e}")

# Commands sent from consumer to template via pipe
CMD_FORK = "fork"
CMD_SHUTDOWN = "shutdown"


def _template_main(
    pipe: Connection,
) -> None:
    """
    Entry point for the template process.

    Loads all heavy dependencies, installs import hooks, then waits
    for fork commands on the pipe. This function runs in the template
    process (spawned via multiprocessing.spawn).

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

        try:
            import sqlalchemy  # noqa: F401
        except ImportError:
            logger.warning("sqlalchemy not available — skipping preload")

        # Execution infrastructure
        try:
            from src.services.execution.virtual_import import install_virtual_import_hook
            from src.services.execution.simple_worker import install_requirements

            # Install user packages (pip install from requirements.txt)
            install_requirements()

            # Ensure user site-packages is in sys.path
            import site
            user_site = site.getusersitepackages()
            if site.ENABLE_USER_SITE and os.path.exists(user_site) and user_site not in sys.path:
                sys.path.insert(0, user_site)
                logger.info(f"Added user site-packages to sys.path: {user_site}")

            # Install virtual import hook for workspace modules
            install_virtual_import_hook()
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
    # - AFTER install_requirements() which needs BIFROST_S3_* for cold-cache
    #   requirements fetch (legacy path) — safe to remove now.
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

    logger.info("Template process ready — all dependencies loaded")
    pipe.send({"status": "ready", "pid": os.getpid()})

    # ----- Fork loop -----
    # Single-threaded, no event loop. Just wait for commands and fork.
    while True:
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

        if cmd.get("action") == CMD_FORK:
            worker_id = cmd.get("worker_id", "unknown")
            persistent = cmd.get("persistent", False)
            work_recv: Connection = cmd["work_recv"]
            result_send: Connection = cmd["result_send"]
            _handle_fork_request(pipe, worker_id, persistent, work_recv, result_send)

    logger.info("Template process exiting")


def _handle_fork_request(
    pipe: Connection,
    worker_id: str,
    persistent: bool,
    work_recv: Connection,
    result_send: Connection,
) -> None:
    """
    Handle a fork request: fork and wire up pre-created pipe connections.

    The consumer creates two Pipe() pairs before calling fork():
      - work pipe:   consumer writes via work_send; child reads via work_recv
      - result pipe: child writes via result_send; consumer reads via result_recv

    The consumer sends work_recv and result_send to us (picklable Connections).
    We fork, the child inherits them, we close them in the parent and
    reply with just the child_pid.

    Args:
        pipe: Control pipe to send response back to consumer.
        worker_id: ID to assign to the forked child worker.
        persistent: If True, child loops for multiple executions.
                    If False, child runs one execution and exits.
        work_recv: Read end of work pipe (child reads execution IDs from here).
        result_send: Write end of result pipe (child writes results here).
    """
    child_pid = os.fork()

    if child_pid > 0:
        # ----- Parent (template) -----
        # Close the child-side connections — the child owns them now
        try:
            work_recv.close()
        except (OSError, BrokenPipeError) as e:
            # Already closed — ignore
            logger.debug(f"parent: work_recv.close ignored: {e}")
        try:
            result_send.close()
        except (OSError, BrokenPipeError) as e:
            # Already closed — ignore
            logger.debug(f"parent: result_send.close ignored: {e}")

        # Send child PID back to consumer
        pipe.send({
            "status": "forked",
            "child_pid": child_pid,
            "worker_id": worker_id,
        })
    else:
        # ----- Child -----
        # Close the template's control pipe — child doesn't need it
        try:
            pipe.close()
        except (OSError, BrokenPipeError) as e:
            # Already closed in parent post-fork — ignore
            logger.debug(f"child: pipe.close ignored: {e}")

        # Run the worker function (this blocks until the child exits)
        _run_forked_child(work_recv, result_send, worker_id, persistent)
        os._exit(0)


def _run_forked_child(
    work_recv: Connection,
    result_send: Connection,
    worker_id: str,
    persistent: bool,
) -> None:
    """
    Entry point for a forked child process.

    The child inherits all loaded modules from the template via COW.
    It creates its own event loop and Redis connection fresh.

    Communication uses raw Connection objects (Pipe ends) that were
    inherited via fork — no pickling required.

    Args:
        work_recv: Read end of work pipe; receives execution_ids via .recv().
        result_send: Write end of result pipe; sends result dicts via .send().
        worker_id: Identifier for logging.
        persistent: If True, loop for multiple executions. If False, run once.
    """
    import gc

    # Reconfigure logging for this child
    logging.basicConfig(
        level=logging.INFO,
        format=f"[{worker_id}] %(levelname)s - %(message)s",
        force=True,
    )

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

            execution_id = work_recv.recv()

            if execution_id is None:
                continue

            logger.info(f"Worker {worker_id} processing execution: {execution_id[:8]}...")

            # NOTE: workspace-module eviction is done INSIDE _execute_sync /
            # _execute_async, AFTER the execution's Solution context is read from
            # Redis and activated. Clearing here (before the context exists) ran
            # the cross-solution eviction blind and could let a prior install's
            # same-name module survive, breaking multi-install isolation (Codex #9).

            # Execute
            try:
                from src.services.execution.simple_worker import _execute_sync
                result = _execute_sync(execution_id, worker_id)
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

            # Force GC before measuring RSS
            gc.collect()

            # Report current RSS
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

    logger.info(f"Worker {worker_id} exiting")


class TemplateProcess:
    """
    Manages the lifecycle of the template process.

    The template process is a long-lived, single-threaded process that
    holds all heavy dependencies in memory and forks children on request.
    """

    def __init__(self) -> None:
        self._process: Any = None  # multiprocessing.Process or SpawnProcess
        self._pipe: Connection | None = None
        self.pid: int | None = None

    def start(self) -> None:
        """
        Spawn the template process and wait for it to be ready.

        Blocks until the template has loaded all dependencies and
        signaled ready, or raises if startup fails.
        """
        if self._process is not None and self._process.is_alive():
            return  # Already running

        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = multiprocessing.Pipe()

        self._process = ctx.Process(
            target=_template_main,
            args=(child_conn,),
            name="template-process",
        )
        self._process.start()
        self._pipe = parent_conn

        # Wait for ready signal (with timeout)
        if not parent_conn.poll(timeout=120):
            self._process.kill()
            raise RuntimeError("Template process failed to start within 120 seconds")

        msg = parent_conn.recv()
        if msg.get("status") == "error":
            raise RuntimeError(f"Template process startup failed: {msg.get('error')}")

        self.pid = msg.get("pid", self._process.pid)
        logger.info(f"Template process ready (PID={self.pid})")

    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
    ) -> tuple[int, _SendQueue, _RecvQueue]:
        """
        Request the template to fork a new child worker.

        Creates two Pipe pairs before sending the fork command. The
        pipe connections are picklable and are sent to the template
        process, which passes them to the forked child via fork
        inheritance. The consumer keeps the parent-side ends.

        Args:
            worker_id: Identifier for the new worker (for logging).
            persistent: If True, child loops for multiple executions.
                        If False (default), child runs one execution and exits.

        Returns:
            Tuple of (child_pid, work_queue, result_queue).
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

        # Send fork command with child-side connections (picklable)
        self._pipe.send({
            "action": CMD_FORK,
            "worker_id": worker_id,
            "persistent": persistent,
            "work_recv": work_recv,
            "result_send": result_send,
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

    def shutdown(self) -> None:
        """Send shutdown command to template and wait for it to exit."""
        if self._pipe is not None:
            try:
                self._pipe.send({"action": CMD_SHUTDOWN})
            except (OSError, BrokenPipeError) as e:
                # Template already exited — no need to send shutdown
                logger.debug(f"template pipe closed before shutdown send: {e}")

        if self._process is not None:
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=5)

        self._pipe = None
        self._process = None
        self.pid = None

    def is_alive(self) -> bool:
        """Check if the template process is still running."""
        if self._process is None:
            return False
        return self._process.is_alive()
