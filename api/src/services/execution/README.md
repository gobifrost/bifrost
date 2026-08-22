# Execution Engine

Distributed, process-pooled execution system with Redis-first architecture for running workflows, scripts, and data providers in isolated processes.

## Architecture Overview

```
                          API Request
                               |
                               v
+---------------------------+  |  +---------------------------+
|        service.py         |  |  |      async_executor.py    |
|  - Workflow lookup        |<-+->|  - Store pending in Redis |
|  - Metadata resolution    |     |  - Publish to RabbitMQ    |
|  - Sync/async dispatch    |     |  - Return execution_id    |
+---------------------------+     +---------------------------+
                                              |
                                              v
                                     +----------------+
                                     |   RabbitMQ     |
                                     |    Queue       |
                                     +----------------+
                                              |
                                              v
+------------------------------------------------------------------+
|                  workflow_execution.py (Consumer)                 |
|  - Read pending execution from Redis                              |
|  - Create PostgreSQL record (RUNNING)                             |
|  - Pre-warm SDK cache                                             |
|  - Route to ProcessPoolManager                                    |
+------------------------------------------------------------------+
                                              |
                                              v
+------------------------------------------------------------------+
|                    process_pool.py (ProcessPoolManager)           |
|  - Fork one-shot children on demand                               |
|  - Cap concurrent children at max_workers                         |
|  - Monitor timeouts and crashes                                   |
|  - Heartbeat publishing for UI visibility                         |
+------------------------------------------------------------------+
                                              |
                        +---------------------+---------------------+
                        |                     |                     |
                        v                     v                     v
                 +-----------+         +-----------+         +-----------+
                 |  Worker   |         |  Worker   |         |  Worker   |
                 | Process 1 |         | Process 2 |         | Process N |
                 +-----------+         +-----------+         +-----------+
                        |
                        v
+------------------------------------------------------------------+
|                     simple_worker.py                              |
|  - Isolated subprocess for user code                              |
|  - Receive context from the parent's private pipe                 |
|  - Clear workspace modules (pick up code changes)                 |
|  - Execute via engine.py                                          |
|  - Return one result, then exit                                   |
+------------------------------------------------------------------+
                        |
                        v
+------------------------------------------------------------------+
|                        engine.py                                  |
|  - Unified execution for workflows, scripts, data providers       |
|  - Set up SDK context (bifrost._context)                          |
|  - Variable capture via sys.settrace()                            |
|  - Real-time log streaming to Redis                               |
|  - Type coercion for parameters                                   |
+------------------------------------------------------------------+
                        |
                        v
                 Result via Queue
                        |
                        v
+------------------------------------------------------------------+
|               workflow_execution.py (Result Handler)              |
|  - Update PostgreSQL with result                                  |
|  - Flush SDK writes (Redis -> Postgres)                           |
|  - Flush logs (Redis Stream -> Postgres)                          |
|  - Publish WebSocket updates                                      |
|  - Push sync result to Redis (for BLPOP)                          |
|  - Cleanup Redis keys                                             |
+------------------------------------------------------------------+
```

## Key Files

| File | Responsibility |
|------|----------------|
| `service.py` | High-level orchestration. Workflow lookup by ID, metadata caching (Redis-first), sync/async dispatch routing. Entry point for `run_workflow()` and `run_code()`. |
| `engine.py` | Unified execution engine. Handles workflows, inline scripts, and data providers. Sets up SDK context, captures variables via `sys.settrace()`, streams logs to Redis, handles data provider caching. |
| `async_executor.py` | Queue management. Stores pending execution in Redis, publishes minimal message to RabbitMQ, returns execution ID immediately (<100ms target). |
| `process_pool.py` | One-shot child lifecycle management. Forks on demand up to `max_workers`, handles timeouts (SIGTERM -> SIGKILL), detects crashes, and publishes heartbeats. |
| `simple_worker.py` | Isolated subprocess entry point. Receives one parent-assembled context over a private pipe, clears workspace modules, delegates to `engine.py`, returns one result, and exits. |
| `workflow_execution.py` | RabbitMQ consumer. Creates PostgreSQL records, pre-warms SDK cache, routes to process pool, handles results (success/failure), flushes data to Postgres, publishes WebSocket updates. |

## Execution States

```
PENDING     API accepted request, queued in RabbitMQ
    |
    v
RUNNING     Consumer picked up, worker executing
    |
    +---> SUCCESS              Completed successfully
    |
    +---> FAILED               Execution error (exception thrown)
    |
    +---> COMPLETED_WITH_ERRORS  Returned {success: false}
    |
    +---> TIMEOUT              Exceeded timeout_seconds
    |
    +---> CANCELLED            User cancelled via API
```

## Data Flow

### Async Execution (Default)

1. API calls `run_workflow()` or `run_code()`
2. `async_executor.py` stores pending execution in Redis
3. Minimal message published to RabbitMQ queue
4. API returns `{execution_id, status: "Pending"}` immediately
5. Consumer reads from RabbitMQ, fetches context from Redis
6. Consumer creates PostgreSQL record with `RUNNING` status
7. Consumer routes to `ProcessPoolManager`
8. Worker process executes code, returns result via queue
9. Consumer updates PostgreSQL, flushes logs/writes, publishes WebSocket update
10. Client receives update via WebSocket subscription

### Sync Execution (Tool Calls, `sync=True`)

1. Steps 1-8 same as async
2. Consumer pushes result to Redis list: `bifrost:result:{execution_id}`
3. API waits on `BLPOP` for result (up to timeout)
4. API returns complete result to caller

```python
# Sync execution with BLPOP
result = await redis_client.wait_for_result(execution_id, timeout_seconds=1800)
```

## SDK Context Injection

The execution engine injects context for the Bifrost SDK:

```python
# engine.py sets up context before execution
from bifrost._context import set_execution_context

context = ExecutionContext(
    user_id=request.caller.user_id,
    email=request.caller.email,
    organization=request.organization,
    execution_id=request.execution_id,
    _config=request.config,      # Integration credentials
    startup=request.startup,     # Launch workflow results
    roi=roi,                     # ROI tracking
)
set_execution_context(context)
```

Workflows access via:
```python
from bifrost import context

# Available in @workflow functions
context.user_id
context.organization.name
context.startup["launch_workflow_result"]
```

## Error Handling

### Timeouts

Process pool monitors execution duration:

```python
# process_pool.py
if elapsed > exec_info.timeout_seconds:
    # 1. Send SIGTERM for graceful shutdown
    os.kill(pid, signal.SIGTERM)

    # 2. Wait grace period
    await asyncio.sleep(graceful_shutdown_seconds)

    # 3. Force kill if still running
    os.kill(pid, signal.SIGKILL)

    # 4. Report timeout via callback
    await on_result({
        "execution_id": exec_info.execution_id,
        "success": False,
        "error_type": "TimeoutError",
    })
```

### Process Crashes

Pool detects crashed processes and reports any interrupted execution. The
next request forks a fresh one-shot child:

```python
# process_pool.py
if not handle.is_alive and handle.state != ProcessState.KILLED:
    # Report crash if execution was in progress
    if handle.current_execution:
        await _report_crash(handle.current_execution)

    # The next route_execution call forks on demand
```

### Cancellation

Cancellation requests via Redis pub/sub:

```python
# Client publishes to bifrost:cancel channel
await redis.publish("bifrost:cancel", {"execution_id": "..."})

# Pool listens and kills the process
await _handle_cancel_request(execution_id)
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `max_workers` | 10 | Maximum worker processes |
| `execution_timeout_seconds` | 300 | Default timeout per execution |
| `graceful_shutdown_seconds` | 5 | Time between SIGTERM and SIGKILL |
| `worker_heartbeat_interval_seconds` | 10 | Heartbeat publish interval |
| `worker_registration_ttl_seconds` | 30 | Redis registration TTL |

Environment variables:
```bash
BIFROST_MAX_WORKERS=10
BIFROST_EXECUTION_TIMEOUT_SECONDS=300
```

## Template Recycling

Execution children are never reused. After a package install or manual recycle
request, the pool lets active one-shot children drain and restarts the
long-lived import template. The next execution forks from the new template.

## Redis Keys

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `bifrost:pending:{execution_id}` | Pending execution context | 1 hour |
| `bifrost:exec:{execution_id}:context` | Worker process context | 1 hour |
| `bifrost:result:{execution_id}` | Sync execution result (BLPOP) | 1 hour |
| `bifrost:pool:{worker_id}` | Worker registration/heartbeat | 30 seconds |
| `bifrost:logs:{execution_id}` | Real-time log stream | Until flush |
| `bifrost:workflow:metadata:{workflow_id}` | Cached workflow metadata | 5 minutes |
