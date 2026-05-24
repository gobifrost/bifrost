# Harden Pool Requirements Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the worker process-pool requirements install resilient so one unbuildable package can't silently strip the entire workflow runtime, surface install failures to platform admins via a notification, and replace the duplicated-per-worker install stream with a single fleet-aware `(x/y) workers` progress summary.

**Architecture:** Today `install_requirements()` runs `pip install -r requirements.txt` as a single all-or-nothing command and returns `None`, swallowing failures. We change it to (1) attempt the batch install, and on failure (2) fall back to installing each package individually so good packages still land, and (3) return a structured `RequirementsInstallResult` describing what succeeded/failed. The async caller in `process_pool.py` (which already has an event loop + Redis) consumes that result and, when any package failed, publishes a deduplicated admin notification via the existing `NotificationService`.

**Tech Stack:** Python 3.11 (the prod runtime; worker image), `subprocess`/`pip`, existing `NotificationService` (Redis-backed, `for_admins=True`), pytest.

---

## Background (why this change)

Incident 2026-05-24: workers restarted and every workflow importing a runtime dep began failing with `No module named 'openrouter'` / `litellm` / `reportlab`. Root cause: `requirements.txt` contained `xhtml2pdf` (an unused leftover), which pulls `pycairo` transitively; `pycairo` has no wheel for the worker platform and must build from source, but the worker image has **no C compiler** (`cc`/`gcc`). `pip install -r` is all-or-nothing, so the `metadata-generation-failed` on `pycairo` aborted the install of **all 13 packages**. `install_requirements()` logged a `warning` and the pool started anyway — so the failure was invisible until workflows failed.

The data-level fix (removing `xhtml2pdf` from the workspace `requirements.txt`) is being handled separately. **This plan is the code-level hardening** so a future bad package degrades gracefully and is visible.

## File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `api/src/services/execution/simple_worker.py` | `install_requirements()` — batch-then-per-package install, returns structured result | Modify |
| `api/src/services/execution/process_pool.py` | Two call sites of `install_requirements` — consume result, fire admin notification on failure | Modify (lines ~463, ~983) |
| `api/tests/unit/execution/test_simple_worker_install.py` | Unit tests for the new install logic (subprocess mocked) | Create |
| `api/tests/unit/execution/test_process_pool.py` | Test that a failed-package result triggers an admin notification | Modify (existing file) |

## Design notes / constraints

- **Runtime is Python 3.11** in prod workers — but `install_requirements()` imports must stay stdlib + existing project deps. No new third-party deps.
- `install_requirements()` runs via `await asyncio.to_thread(install_requirements)` from `ProcessPoolManager.start()` and `recycle_all()`. It is therefore **synchronous** and must stay sync (it runs in a thread). It must **not** do async/Redis work itself — it returns data; the async caller notifies.
- Notification convention (from `api/src/services/file_storage/diagnostics.py:95` and `agent_executor.py:620`): `get_notification_service().create_notification(user_id="system", request=NotificationCreate(category=..., title=..., description=...), for_admins=True)`. Category `NotificationCategory.PACKAGE_INSTALL` already exists for this purpose.
- **Dedup:** workers recycle and there are 6 of them; use `find_admin_notification_by_title(title, category)` before creating, so we don't spawn 6 identical notifications. Title is stable per failure set.
- **Per-package fallback** only runs when the batch fails, to keep the happy path fast (one pip invocation). Per-package uses `--no-deps`? No — we DO want deps for good packages; run each line as its own `pip install <line>` so a bad transitive dep only fails that one line.

---

### Task 1: Structured result + resilient install in `simple_worker.py`

**Files:**
- Modify: `api/src/services/execution/simple_worker.py:38-91`
- Test: `api/tests/unit/execution/test_simple_worker_install.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/unit/execution/test_simple_worker_install.py`:

```python
"""Unit tests for install_requirements resilient install + result reporting."""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

from src.services.execution.simple_worker import (
    install_requirements,
    RequirementsInstallResult,
)


def _completed(returncode: int, stderr: str = "", stdout: str = "") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stderr = stderr
    m.stdout = stdout
    return m


def test_no_requirements_returns_empty_result():
    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=None
    ):
        result = install_requirements()
    assert isinstance(result, RequirementsInstallResult)
    assert result.attempted == []
    assert result.failed == []
    assert result.ok is True


def test_batch_success_marks_all_installed():
    content = "anthropic\nlitellm\nreportlab\n"
    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch("subprocess.run", return_value=_completed(0)) as run:
        result = install_requirements()
    # Only the batch invocation runs on success (no per-package fallback)
    assert run.call_count == 1
    assert result.ok is True
    assert set(result.installed) == {"anthropic", "litellm", "reportlab"}
    assert result.failed == []


def test_batch_failure_falls_back_to_per_package_and_isolates_bad_dep():
    content = "anthropic\nxhtml2pdf\nreportlab\n"

    def fake_run(cmd, **kwargs):
        # Batch install (cmd contains "-r") fails; per-package: xhtml2pdf fails,
        # others succeed.
        joined = " ".join(cmd)
        if "-r" in cmd:
            return _completed(1, stderr="metadata-generation-failed: pycairo")
        if "xhtml2pdf" in joined:
            return _completed(1, stderr="ERROR: Unknown compiler(s): cc gcc")
        return _completed(0)

    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch("subprocess.run", side_effect=fake_run):
        result = install_requirements()

    assert result.ok is False
    assert set(result.installed) == {"anthropic", "reportlab"}
    assert [f.package for f in result.failed] == ["xhtml2pdf"]
    assert "Unknown compiler" in result.failed[0].error


def test_comments_and_blank_lines_are_ignored():
    content = "# a comment\n\nanthropic\n  \n# another\nlitellm\n"
    with patch(
        "src.core.requirements_cache.get_requirements_sync", return_value=content
    ), patch("subprocess.run", return_value=_completed(0)):
        result = install_requirements()
    assert set(result.attempted) == {"anthropic", "litellm"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/execution/test_simple_worker_install.py -v`
Expected: FAIL — `ImportError: cannot import name 'RequirementsInstallResult'` (and `install_requirements` currently returns `None`).

- [ ] **Step 3: Write minimal implementation**

In `api/src/services/execution/simple_worker.py`, add the result dataclasses near the top (after the imports, before `install_requirements`):

```python
from dataclasses import dataclass, field


@dataclass
class FailedPackage:
    """One requirements line that failed to install."""

    package: str
    error: str


@dataclass
class RequirementsInstallResult:
    """Outcome of a pool requirements install attempt.

    `ok` is True when nothing failed (including the trivial no-requirements
    case). `installed` + `failed` partition `attempted`.
    """

    attempted: list[str] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)
    failed: list[FailedPackage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed
```

Replace the body of `install_requirements()` (lines 38-91) with:

```python
def _parse_requirement_lines(content: str) -> list[str]:
    """Return non-comment, non-blank requirement specifiers from file content."""
    lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _pip_install(args: list[str]) -> "subprocess.CompletedProcess[str]":
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "pip", "install", *args, "--quiet"],
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout
    )


def install_requirements() -> RequirementsInstallResult:
    """
    Install packages from requirements.txt resiliently.

    Called once at pool startup (and on recycle_all) to ensure user-installed
    packages persist across container restarts. Reads requirements via
    get_requirements_sync() (Redis → S3 fallback).

    Strategy: attempt a single batch ``pip install -r`` for speed. If the batch
    fails (e.g. one package can't build), fall back to installing each
    requirement individually so a single bad package no longer strips the whole
    runtime. Returns a structured result; the async caller surfaces failures.

    This function never raises — failures are captured in the returned result.
    """
    import subprocess
    import tempfile

    from src.core.requirements_cache import get_requirements_sync

    result = RequirementsInstallResult()

    content = get_requirements_sync()
    if not content:
        logger.info("[pool] No requirements.txt found")
        return result

    packages = _parse_requirement_lines(content)
    result.attempted = list(packages)
    if not packages:
        return result

    # Fast path: one batch install.
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            temp_path = f.name

        logger.info(f"[pool] Installing {len(packages)} packages from requirements.txt")
        batch = _pip_install(["-r", temp_path])
        if batch.returncode == 0:
            logger.info(f"[pool] Installed {len(packages)} packages from requirements.txt")
            result.installed = list(packages)
            return result

        logger.warning(
            "[pool] Batch pip install failed; falling back to per-package install. "
            f"pip output: {batch.stderr or batch.stdout}"
        )
    except subprocess.TimeoutExpired:
        logger.warning("[pool] Batch pip install timed out; trying per-package install")
    except Exception as e:
        logger.warning(f"[pool] Batch install error ({e}); trying per-package install")
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError as e:
                logger.debug(f"[pool] could not remove temp requirements file {temp_path}: {e}")

    # Fallback: install each requirement on its own so one bad package only
    # fails itself.
    for pkg in packages:
        try:
            single = _pip_install([pkg])
            if single.returncode == 0:
                result.installed.append(pkg)
            else:
                err = (single.stderr or single.stdout or "").strip()
                result.failed.append(FailedPackage(package=pkg, error=err[:2000]))
                logger.warning(f"[pool] Failed to install '{pkg}': {err[:500]}")
        except subprocess.TimeoutExpired:
            result.failed.append(FailedPackage(package=pkg, error="pip install timed out (300s)"))
            logger.warning(f"[pool] Timed out installing '{pkg}'")
        except Exception as e:  # noqa: BLE001 - per-package install must never abort the loop
            result.failed.append(FailedPackage(package=pkg, error=str(e)))
            logger.warning(f"[pool] Error installing '{pkg}': {e}")

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/execution/test_simple_worker_install.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/execution/simple_worker.py api/tests/unit/execution/test_simple_worker_install.py
git commit -m "feat(pool): resilient per-package requirements install with structured result"
```

---

### Task 2: Surface install failures as an admin notification

**Files:**
- Modify: `api/src/services/execution/process_pool.py` (call sites ~463 and ~983; add a helper)
- Test: `api/tests/unit/execution/test_process_pool.py`

- [ ] **Step 1: Write the failing test**

Add to `api/tests/unit/execution/test_process_pool.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from src.services.execution.simple_worker import (
    RequirementsInstallResult,
    FailedPackage,
)


@pytest.mark.asyncio
async def test_failed_install_notifies_admins():
    """A result with failed packages creates a deduped admin notification."""
    from src.services.execution.process_pool import _notify_requirements_failures

    result = RequirementsInstallResult(
        attempted=["anthropic", "xhtml2pdf"],
        installed=["anthropic"],
        failed=[FailedPackage(package="xhtml2pdf", error="Unknown compiler(s): cc")],
    )

    svc = AsyncMock()
    svc.find_admin_notification_by_title.return_value = None  # no existing dup
    with patch(
        "src.services.execution.process_pool.get_notification_service",
        return_value=svc,
    ):
        await _notify_requirements_failures(result)

    svc.create_notification.assert_awaited_once()
    kwargs = svc.create_notification.await_args.kwargs
    assert kwargs["for_admins"] is True
    assert kwargs["user_id"] == "system"
    assert "xhtml2pdf" in kwargs["request"].description


@pytest.mark.asyncio
async def test_failed_install_dedups_existing_notification():
    from src.services.execution.process_pool import _notify_requirements_failures

    result = RequirementsInstallResult(
        attempted=["xhtml2pdf"],
        installed=[],
        failed=[FailedPackage(package="xhtml2pdf", error="boom")],
    )
    svc = AsyncMock()
    svc.find_admin_notification_by_title.return_value = object()  # dup exists
    with patch(
        "src.services.execution.process_pool.get_notification_service",
        return_value=svc,
    ):
        await _notify_requirements_failures(result)

    svc.create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_install_does_not_notify():
    from src.services.execution.process_pool import _notify_requirements_failures

    result = RequirementsInstallResult(
        attempted=["anthropic"], installed=["anthropic"], failed=[]
    )
    svc = AsyncMock()
    with patch(
        "src.services.execution.process_pool.get_notification_service",
        return_value=svc,
    ):
        await _notify_requirements_failures(result)

    svc.find_admin_notification_by_title.assert_not_awaited()
    svc.create_notification.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/execution/test_process_pool.py -k requirements_failures -v`
Expected: FAIL — `ImportError: cannot import name '_notify_requirements_failures'`.

- [ ] **Step 3: Write minimal implementation**

In `api/src/services/execution/process_pool.py`, add imports near the other `src.` imports at the top:

```python
from src.models import NotificationCreate
from src.models.contracts.notifications import NotificationCategory, NotificationStatus
from src.services.notification_service import get_notification_service
```

(If `NotificationCreate` is not exported from `src.models`, import it from `src.models.contracts.notifications` instead — verify with `rg "class NotificationCreate" api/src/models`.)

Add the helper function near the other module-level helpers (after the imports, before the class, or as a module function):

```python
async def _notify_requirements_failures(result: "RequirementsInstallResult") -> None:
    """Publish a deduped admin notification when requirements failed to install.

    No-op when the install fully succeeded. Best-effort: notification errors
    are logged, never raised, so install/recycle is never blocked by it.
    """
    if result.ok:
        return

    failed_names = ", ".join(f.package for f in result.failed)
    title = "Workflow package install failed"
    description = (
        f"{len(result.failed)} package(s) failed to install on workers: "
        f"{failed_names}. Workflows importing them will fail until fixed."
    )
    try:
        service = get_notification_service()
        existing = await service.find_admin_notification_by_title(
            title, NotificationCategory.PACKAGE_INSTALL
        )
        if existing is not None:
            logger.info("[pool] requirements-failure notification already exists; skipping")
            return
        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.PACKAGE_INSTALL,
                title=title,
                description=description[:500],
                metadata={
                    "failed": [
                        {"package": f.package, "error": f.error[:500]}
                        for f in result.failed
                    ],
                    "installed": result.installed,
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.FAILED,
        )
        logger.warning(f"[pool] Notified admins of requirements install failures: {failed_names}")
    except Exception as e:  # noqa: BLE001 - notification must never block the pool
        logger.warning(f"[pool] Could not publish requirements-failure notification: {e}")
```

Add the `RequirementsInstallResult` import to the existing `simple_worker` import line:

```python
from src.services.execution.simple_worker import install_requirements, RequirementsInstallResult
```

Update **both** call sites that currently read `await asyncio.to_thread(install_requirements)`:

At `ProcessPoolManager.start()` (~line 463):

```python
        # Install requirements once (shared filesystem — all child processes inherit)
        install_result = await asyncio.to_thread(install_requirements)
        await _notify_requirements_failures(install_result)
```

At `recycle_all()` (~line 983):

```python
        install_result = await asyncio.to_thread(install_requirements)
        await _notify_requirements_failures(install_result)
        self._update_requirements_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/execution/test_process_pool.py -k requirements_failures -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full existing pool test file to check for regressions**

Run: `./test.sh tests/unit/execution/test_process_pool.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 6: Commit**

```bash
git add api/src/services/execution/process_pool.py api/tests/unit/execution/test_process_pool.py
git commit -m "feat(pool): notify admins when workflow package install fails"
```

---

### Task 3: Verify quality gates

**Files:** none (verification only)

- [ ] **Step 1: Type check**

Run: `cd api && pyright src/services/execution/simple_worker.py src/services/execution/process_pool.py`
Expected: 0 errors. (If `subprocess.CompletedProcess[str]` subscripting trips pyright on 3.11, use a plain `subprocess.CompletedProcess` annotation.)

- [ ] **Step 2: Lint**

Run: `cd api && ruff check src/services/execution/ tests/unit/execution/`
Expected: pass. The two `# noqa: BLE001` comments are intentional (broad except must not abort the install loop / block the pool) — keep them with the inline reason per the project's CodeQL/bare-except convention.

- [ ] **Step 3: Run the targeted unit tests once more together**

Run: `./test.sh tests/unit/execution/test_simple_worker_install.py tests/unit/execution/test_process_pool.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit (if any gate required a fix)**

```bash
git add -A && git commit -m "chore(pool): satisfy type/lint gates for install hardening"
```

---

---

## Part 2 — Fleet-aware install progress (fix the duplicated stream)

**Problem:** `PackageInstallConsumer` is a RabbitMQ **fanout** consumer — every worker (6 in prod) receives the same install message and each independently `pubsub_manager.broadcast(...)`s its raw log lines (`Installing …`, `Recycling …`) to the **shared** `package:install` WebSocket channel. The frontend `appendLog`s every one, so each line appears once per worker. No worker knows the fleet size or the others' progress, so dedup-at-source is impossible without coordination.

**Approach (server-side aggregation via Redis):** Each worker writes its *phase* for the current install run into a per-run Redis hash keyed by `worker_id`. After each write it computes an aggregate (how many workers installed / recycled / failed, out of the live worker count from `bifrost:pool:*`) and publishes **one** `progress` message to `package:install`. The frontend renders the latest aggregate as a single rolling line and collapses consecutive identical summaries — so N workers publishing the same aggregate show as one line. Raw per-worker `log` lines are removed from the consumer's happy path.

**Run identity:** the API handler that broadcasts the install message stamps a `run_id` (uuid) on it; all workers share it → one Redis hash per run. If absent (older message), fall back to the literal `"current"` so behavior degrades to a single shared hash rather than crashing.

### New files / changes (Part 2)

| File | Responsibility | Change |
|------|---------------|--------|
| `api/src/services/execution/install_progress.py` | Redis hash read/write + aggregate computation + publish one summary | Create |
| `api/src/jobs/consumers/package_install.py` | Report phases through the aggregator instead of raw per-worker logs | Modify |
| `api/src/routers/packages.py` | Stamp `run_id` on the broadcast message | Modify |
| `client/src/services/websocket.ts` | Add `PackageProgress` type + `onPackageProgress` callback | Modify |
| `client/src/components/editor/PackagePanel.tsx` | Render aggregate progress as one collapsing line | Modify |
| `api/tests/unit/execution/test_install_progress.py` | Unit-test aggregate computation + dedup | Create |

---

### Task 4: Redis-backed install-progress aggregator

**Files:**
- Create: `api/src/services/execution/install_progress.py`
- Test: `api/tests/unit/execution/test_install_progress.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/unit/execution/test_install_progress.py`:

```python
"""Unit tests for the install-progress aggregator."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

from src.services.execution.install_progress import (
    WorkerPhase,
    aggregate_phases,
    summary_line,
)


def test_aggregate_counts_phases_out_of_total():
    phases = {
        "w1": WorkerPhase(phase="installed"),
        "w2": WorkerPhase(phase="installing"),
        "w3": WorkerPhase(phase="failed", package="xhtml2pdf", error="no cc"),
    }
    agg = aggregate_phases(phases, total=4)
    assert agg["total"] == 4
    assert agg["installed"] == 1
    assert agg["installing"] == 1
    assert agg["failed"] == 1
    assert agg["failures"] == [{"worker": "w3", "package": "xhtml2pdf", "error": "no cc"}]


def test_summary_line_installing():
    agg = {"total": 6, "installing": 3, "installed": 0, "recycling": 0,
           "recycled": 0, "failed": 0, "failures": []}
    assert summary_line(agg, action="install") == "Installing on 3/6 workers…"


def test_summary_line_complete_with_failures():
    agg = {"total": 6, "installing": 0, "installed": 5, "recycling": 0,
           "recycled": 5, "failed": 1,
           "failures": [{"worker": "w4", "package": "xhtml2pdf", "error": "no cc"}]}
    line = summary_line(agg, action="install")
    assert "5/6" in line
    assert "xhtml2pdf" in line


@pytest.mark.asyncio
async def test_report_phase_writes_hash_and_publishes_once():
    fake_redis = AsyncMock()
    # hgetall returns this worker's write plus one peer
    fake_redis.hgetall.return_value = {
        "w1": json.dumps({"phase": "installed"}),
        "w2": json.dumps({"phase": "installing"}),
    }
    # scan yields two live pool keys then terminates
    fake_redis.scan.side_effect = [(0, ["bifrost:pool:w1", "bifrost:pool:w2"])]

    published: list[dict] = []

    async def fake_broadcast(channel, message):
        published.append(message)

    from src.services.execution import install_progress as ip
    with patch.object(ip, "_raw_redis", AsyncMock(return_value=fake_redis)), \
         patch.object(ip.pubsub_manager, "broadcast", side_effect=fake_broadcast):
        await ip.report_phase(
            run_id="run1", worker_id="w1", phase="installed", action="install"
        )

    fake_redis.hset.assert_awaited()  # wrote own field
    assert len(published) == 1
    assert published[0]["type"] == "progress"
    assert published[0]["total"] == 2
    assert published[0]["installed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/execution/test_install_progress.py -v`
Expected: FAIL — module `install_progress` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Create `api/src/services/execution/install_progress.py`:

```python
"""Fleet-aware install progress aggregation.

Each worker in the fanout reports its phase for a given install run into a
per-run Redis hash. After each write the worker computes an aggregate over all
reporting workers (out of the live worker count) and publishes ONE summary
message to the shared ``package:install`` WebSocket channel. The frontend
collapses consecutive identical summaries, so N workers reporting the same
aggregate render as a single line.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, cast

from src.core.pubsub import manager as pubsub_manager
from src.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

CHANNEL = "package:install"
_HASH_PREFIX = "bifrost:pkg-install:"
_HASH_TTL_SECONDS = 120
# Phases reported by a worker over an install run.
_PHASES = ("installing", "installed", "recycling", "recycled", "failed")


@dataclass
class WorkerPhase:
    phase: str
    package: str | None = None
    error: str | None = None


async def _raw_redis():  # pragma: no cover - thin accessor, patched in tests
    client = get_redis_client()
    return await client._get_redis()


async def _live_worker_count(redis) -> int:
    """Count pool registration keys (exactly bifrost:pool:{id}, 3 parts)."""
    cursor = 0
    count = 0
    while True:
        cursor, keys = await cast(
            Awaitable[tuple[int, list[str]]],
            redis.scan(cursor, match="bifrost:pool:*", count=100),
        )
        for key in keys:
            if key.count(":") == 2:
                count += 1
        if cursor == 0:
            break
    return count


def aggregate_phases(phases: dict[str, WorkerPhase], total: int) -> dict[str, Any]:
    """Reduce per-worker phases into counts + failure detail."""
    counts = {p: 0 for p in _PHASES}
    failures: list[dict[str, Any]] = []
    for worker_id, wp in phases.items():
        if wp.phase in counts:
            counts[wp.phase] += 1
        if wp.phase == "failed":
            failures.append(
                {"worker": worker_id, "package": wp.package, "error": wp.error}
            )
    # 'recycled' implies install finished on that worker too.
    return {
        "total": max(total, len(phases)),
        "installing": counts["installing"],
        "installed": counts["installed"] + counts["recycling"] + counts["recycled"],
        "recycling": counts["recycling"],
        "recycled": counts["recycled"],
        "failed": counts["failed"],
        "failures": failures,
    }


def summary_line(agg: dict[str, Any], action: str) -> str:
    """Human-readable single line for the terminal view."""
    verb = "Installing" if action == "install" else "Uninstalling"
    total = agg["total"]
    if agg["recycled"] and not agg["installing"]:
        base = f"{verb.replace('ing', 'ed')} on {agg['recycled']}/{total} workers"
    elif agg["installed"] and not agg["installing"]:
        base = f"{verb.replace('ing', 'ed')} on {agg['installed']}/{total} workers"
    else:
        base = f"{verb} on {agg['installing']}/{total} workers…"
    if agg["failed"]:
        pkgs = ", ".join(
            f"{f['worker']}: {f['package']}" for f in agg["failures"] if f.get("package")
        ) or f"{agg['failed']} worker(s)"
        base += f" — {agg['failed']} failed ({pkgs})"
    return base


async def report_phase(
    run_id: str,
    worker_id: str,
    phase: str,
    action: str = "install",
    package: str | None = None,
    error: str | None = None,
) -> None:
    """Write this worker's phase, then publish one aggregate summary.

    Best-effort: never raises (a progress-reporting failure must not abort the
    install).
    """
    try:
        redis = await _raw_redis()
        key = f"{_HASH_PREFIX}{run_id or 'current'}"
        field_val = json.dumps(
            {"phase": phase, "package": package, "error": (error or "")[:500]}
        )
        await redis.hset(key, worker_id, field_val)  # type: ignore[misc]
        await redis.expire(key, _HASH_TTL_SECONDS)

        raw = await cast(Awaitable[dict[str, str]], redis.hgetall(key))
        phases: dict[str, WorkerPhase] = {}
        for wid, val in raw.items():
            try:
                d = json.loads(val)
                phases[wid] = WorkerPhase(
                    phase=d.get("phase", ""),
                    package=d.get("package"),
                    error=d.get("error"),
                )
            except (json.JSONDecodeError, TypeError):
                continue

        total = await _live_worker_count(redis)
        agg = aggregate_phases(phases, total)
        message = {
            "type": "progress",
            "action": action,
            "line": summary_line(agg, action),
            **agg,
        }
        await pubsub_manager.broadcast(CHANNEL, message)
    except Exception as e:  # noqa: BLE001 - progress reporting must never break install
        logger.warning(f"[pkg-install] progress report failed: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/execution/test_install_progress.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/execution/install_progress.py api/tests/unit/execution/test_install_progress.py
git commit -m "feat(pool): fleet-aware install-progress aggregator"
```

---

### Task 5: Wire the consumer + handler to report phases (remove duplicated logs)

**Files:**
- Modify: `api/src/jobs/consumers/package_install.py`
- Modify: `api/src/routers/packages.py` (stamp `run_id` on broadcast)

- [ ] **Step 1: Stamp `run_id` in the packages router broadcast**

In `api/src/routers/packages.py`, find each `publish_broadcast(...)` to the `package-installations` exchange and add a `run_id`. First confirm the call shape:

Run: `rg -n "publish_broadcast|package-installations|EXCHANGE" api/src/routers/packages.py`

Then for each broadcast message dict, add `"run_id": str(uuid4())` (import `from uuid import uuid4` at top if absent). Example (adapt to the actual dict in the file):

```python
from uuid import uuid4
# ...
await publish_broadcast(
    "package-installations",
    {"action": "install", "package": req.name, "version": req.version, "run_id": str(uuid4())},
)
```

If a single API request triggers one logical install, generate the `run_id` once and reuse it for that request's broadcast.

- [ ] **Step 2: Replace per-worker raw logs with phase reports in the consumer**

In `api/src/jobs/consumers/package_install.py`, import the aggregator at top:

```python
from src.services.execution.install_progress import report_phase
```

Add a `worker_id` accessor on the consumer (mirror the pool's source):

```python
import os
# ... in PackageInstallConsumer:
@property
def _worker_id(self) -> str:
    return os.environ.get("HOSTNAME", "unknown")
```

Rewrite `process_message` so it reports phases instead of broadcasting raw per-worker log lines. Replace the body (lines ~202-243) with:

```python
    async def process_message(self, body: dict[str, Any]) -> None:
        action = body.get("action", "install")
        package = body.get("package")
        version = body.get("version")
        run_id = body.get("run_id", "current")
        package_spec = (
            f"{package}=={version}" if package and version else (package or "requirements.txt")
        )
        wid = self._worker_id
        logger.info(f"Processing package {action}: {package_spec} (run={run_id})")

        await report_phase(run_id, wid, phase="installing", action=action)

        if action == "uninstall":
            if not package:
                await report_phase(run_id, wid, phase="failed", action=action,
                                   package="(none)", error="uninstall requires a package name")
                return
            success = await self._pip_uninstall(package)
        elif package:
            success = await self._pip_install(package, version)
        else:
            success = await self._pip_install_requirements()

        if not success:
            await report_phase(run_id, wid, phase="failed", action=action,
                               package=package_spec, error="pip command failed (see worker logs)")
            return

        await report_phase(run_id, wid, phase="recycling", action=action)
        await self._recycle_workers()
        await self._update_pool_packages()
        await report_phase(run_id, wid, phase="recycled", action=action)
        logger.info(f"Package {action} completed on {wid}")
```

Delete the now-unused `_send_log` and `_send_complete` methods (lines ~49-61) — they were the source of the duplicated stream. (Per the project "no dead code" rule, remove them entirely; verify nothing else calls them with `rg -n "_send_log|_send_complete" api/src`.)

- [ ] **Step 3: Run existing consumer tests**

Run: `./test.sh tests/unit/jobs/test_package_install_consumer.py -v`
Expected: PASS. If existing tests assert on `_send_log`/`_send_complete`, update them to assert on `report_phase` being awaited with the expected phase sequence (`installing` → `recycling` → `recycled`, or `failed`). Show the updated assertions in the diff.

- [ ] **Step 4: Commit**

```bash
git add api/src/jobs/consumers/package_install.py api/src/routers/packages.py api/tests/unit/jobs/test_package_install_consumer.py
git commit -m "feat(pool): report install phases via aggregator instead of per-worker logs"
```

---

### Task 6: Frontend renders one collapsing progress line

**Files:**
- Modify: `client/src/services/websocket.ts`
- Modify: `client/src/components/editor/PackagePanel.tsx`
- Test: sibling vitest if one exists for PackagePanel; otherwise add `client/src/components/editor/PackagePanel.progress.test.tsx`

- [ ] **Step 1: Add the `PackageProgress` type + callback in `websocket.ts`**

After the `PackageLog`/`PackageComplete` interfaces (line ~151), add:

```typescript
export interface PackageProgress {
	action: "install" | "uninstall";
	line: string;
	total: number;
	installing: number;
	installed: number;
	recycling: number;
	recycled: number;
	failed: number;
	failures: { worker: string; package: string | null; error: string | null }[];
}
```

Add `{ type: "progress"; ... }` to the `package:install` message union (near line ~441-442):

```typescript
	| {
			type: "progress";
			action: "install" | "uninstall";
			line: string;
			total: number;
			installing: number;
			installed: number;
			recycling: number;
			recycled: number;
			failed: number;
			failures: { worker: string; package: string | null; error: string | null }[];
	  }
```

Add the callback set + register/dispatch, mirroring `onPackageLog` (line ~1290). Add near the other callback sets:

```typescript
	private packageProgressCallbacks = new Set<(p: PackageProgress) => void>();
```

Register method:

```typescript
	onPackageProgress(callback: (p: PackageProgress) => void): () => void {
		this.packageProgressCallbacks.add(callback);
		return () => this.packageProgressCallbacks.delete(callback);
	}
```

In the `package:install` message dispatch switch (where `type: "log"` / `type: "complete"` are handled), add a `case "progress"` that calls each `packageProgressCallbacks` with the message payload. (Find it with `rg -n '"complete"' client/src/services/websocket.ts`.)

- [ ] **Step 2: Render the collapsing line in `PackagePanel.tsx`**

In the subscribe effect (line ~88-132), replace the raw `onPackageLog` append with an `onPackageProgress` handler that appends a log line **only when it differs from the last one** (collapse duplicates):

```typescript
		const lastProgressRef = { current: "" }; // module-scope ref or useRef

		const unsubscribeProgress = webSocketService.onPackageProgress((p) => {
			const id = currentInstallationIdRef.current;
			if (!id) return;
			if (p.line === lastProgressRef.current) return; // collapse identical
			lastProgressRef.current = p.line;
			const store = useExecutionStreamStore.getState();
			store.appendLog(id, {
				level: p.failed > 0 ? "WARNING" : "INFO",
				message: p.line,
				timestamp: new Date().toISOString(),
			});
		});
```

Use a real `useRef<string>("")` declared in the component body for `lastProgressRef` (not an inline object). Drive completion off progress instead of the old `complete` event: when `p.recycled + p.failed >= p.total && p.total > 0`, mark the stream complete with status `Failed` if `p.failed > 0` else `Success`, reusing the existing `completionHandledRef` guard. Keep `onPackageComplete` subscription only if other producers (git ops) still use it; otherwise remove the package-install completion path that relied on it. Remove the `unsubscribeLog`/`onPackageLog` wiring for the package channel (it produced the duplicate lines).

- [ ] **Step 3: Add/adjust a vitest**

If a sibling test exists, add a case; else create `client/src/components/editor/PackagePanel.progress.test.tsx` asserting that two `onPackageProgress` events with the same `line` produce a single appended log, and that `recycled === total` flips the stream to complete. Mock `webSocketService` and `useExecutionStreamStore` per the patterns in neighboring `*.test.tsx`.

Run: `./test.sh client unit -- PackagePanel`
Expected: PASS.

- [ ] **Step 4: Regenerate types + frontend gates**

The progress message is a client-only WS contract (not an OpenAPI schema), so `generate:types` is not required for it. Still run the gates:

Run: `cd client && npm run tsc && npm run lint`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add client/src/services/websocket.ts client/src/components/editor/PackagePanel.tsx client/src/components/editor/PackagePanel.progress.test.tsx
git commit -m "feat(packages): render single fleet-aware install progress line"
```

---

## Self-Review checklist (completed during planning)

1. **Spec coverage:** (a) resilient install so one bad package can't strip the runtime → Task 1 per-package fallback. (b) admin notification on failure → Task 2. (c) replace duplicated per-worker stream with `(x/y) workers` summary → Tasks 4–6 (aggregator, consumer wiring, frontend collapse). All covered.
2. **Placeholder scan:** all code steps contain full code; remaining conditionals ("if `NotificationCreate` not exported", "find the dispatch switch", "if a sibling test exists") each give the exact `rg`/command to resolve them against the live code rather than guessing.
3. **Type consistency:** `RequirementsInstallResult`/`FailedPackage`/`_notify_requirements_failures` consistent across Tasks 1–2. `WorkerPhase` (`phase`/`package`/`error`), `aggregate_phases(phases,total)→dict`, `summary_line(agg,action)`, `report_phase(run_id,worker_id,phase,action,package,error)`, and the `progress` message fields (`action`/`line`/`total`/`installing`/`installed`/`recycling`/`recycled`/`failed`/`failures`) match across Task 4 (backend), Task 5 (producer), and Task 6 (`PackageProgress` TS interface). `run_id` is produced in Task 5 step 1 and consumed in Task 5 step 2 + Task 4.

## Out of scope (do NOT do here)

- Removing `xhtml2pdf` from the workspace `requirements.txt` (data fix, handled separately).
- Adding a C compiler / Cairo headers to the worker image.
- Frontend changes to render the failure *notification* (the existing notifications UI already renders `PACKAGE_INSTALL` admin notifications). Note: the install *progress stream* (Task 6) is a different surface (the editor PackagePanel terminal) and IS in scope.
- Reworking the fanout broadcast mechanism itself — Part 2 keeps fanout, only changing what each worker publishes (phase reports, not raw logs) and stamping a shared `run_id`.
