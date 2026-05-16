# `bifrost files` CLI Verbs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing file SDK (read/write/list/delete/exists + the platform's existing `/api/files/search` endpoint) as `bifrost files <verb>` CLI commands, and ensure the `bifrost` CLI is actually on PATH inside worker containers so workflows can shell out to it.

**Architecture:** Pure CLI/SDK surface work over already-shipped HTTP endpoints. No new platform capabilities, no schema changes, no auth model changes. Three pieces: (1) a `/usr/local/bin/bifrost` shim in both Dockerfiles that `exec`s `python -m bifrost`, (2) one new SDK method `files.search()` wrapping `/api/files/search`, (3) one new command module `api/bifrost/commands/files.py` registering a `files` Click sub-group with verbs `read`, `write`, `list`, `delete`, `exists`, `search`.

**Tech Stack:** Python 3.14, Click (CLI), httpx (HTTP), pytest (tests), Docker (worker image).

**Spec reference:** `/home/jack/Sync/Projects/buildfrost/backend-ask.md`

**Out of scope (do not implement):**
- New auth primitives — engine token via `authenticate_engine()` already works in workers via the JSON-credentials fallback in `api/bifrost/credentials.py:11`.
- Server-side grep refactor — `/api/files/search` (`api/src/routers/files.py:989`) already implements regex/glob search via `search_files_db` in `api/src/services/editor/search.py:89`. We're exposing it, not rebuilding it.
- Local-mirror, batch ops, file watcher, `stat` with mtime/etag (the SDK only surfaces `exists`; we expose that as-is).

---

## File Structure

**Created:**
- `api/bifrost/commands/files.py` — Click sub-group with 6 verbs (read/write/list/delete/exists/search). Mirrors the structure of `api/bifrost/commands/configs.py`. Each verb is a thin wrapper around an `api/bifrost/files.py` SDK method.
- `api/tests/unit/test_cli_files.py` — Unit tests for each CLI verb. Mocks `BifrostClient.get_instance` like `api/tests/unit/test_cli_tables.py:67`. No network, no auth.
- `api/tests/e2e/platform/test_cli_files.py` — End-to-end test: real CLI process invoked via `subprocess.run(["bifrost", "files", ...])` against the running API. Includes a `which bifrost` smoke test that proves the Dockerfile shim works.

**Modified:**
- `api/bifrost/files.py` — Add a `files.search()` async method that POSTs to `/api/files/search` and returns `SearchResponse`.
- `api/bifrost/commands/__init__.py` — Register `files_group` in `ENTITY_GROUPS`.
- `api/Dockerfile.dev` — Add a one-line `bifrost` shim script.
- `api/Dockerfile` — Same shim in the production image.

**No changes to:**
- `api/src/routers/files.py` (endpoints already exist)
- `api/src/services/editor/search.py` (search backend already exists)
- `api/src/models/contracts/editor.py` (SearchRequest/SearchResponse already exist)
- Auth, RBAC, or scope handling

---

## Task 1: Install the `bifrost` CLI shim in dev worker image

**Files:**
- Modify: `api/Dockerfile.dev` (add lines after line 51, before the user creation at line 54)

**Context:** `python -m bifrost` already works in the container because `PYTHONPATH=/app` includes `/app/bifrost/`. We just need a binary on PATH. A shell shim is simpler than `pip install -e api/` (which would re-resolve `requirements.lock` and is fragile under `--require-hashes`).

- [ ] **Step 1: Verify current state — no `bifrost` on PATH**

Run from worktree root:
```bash
docker exec bifrost-debug-d301cb77-worker-1 bash -c 'which bifrost; echo "exit=$?"'
```
Expected: `exit=1` (command not found). Note the actual container name from `docker ps` may differ — substitute the running worker container name.

If the dev stack isn't up, skip the verification step and proceed to step 2; the smoke test at the end of Task 4 will catch regressions.

- [ ] **Step 2: Add the shim to `api/Dockerfile.dev`**

Insert immediately after `COPY --from=builder /usr/local/bin /usr/local/bin` (line 51) and before `# Create non-root user` (line 53):

```dockerfile
# Install `bifrost` CLI shim. The bifrost Python package is mounted at
# /app/bifrost/ via PYTHONPATH=/app, so `python -m bifrost` works. The shim
# below exposes that as the `bifrost` console script on PATH, matching the
# console-script entry declared in api/pyproject.toml. Workflows shell out
# to `bifrost <verb>` from worker subprocesses (see Buildfrost spec at
# Sync/Projects/buildfrost/backend-ask.md).
RUN printf '#!/bin/sh\nexec python -m bifrost "$@"\n' > /usr/local/bin/bifrost \
    && chmod +x /usr/local/bin/bifrost
```

- [ ] **Step 3: Rebuild and verify**

```bash
docker compose -f docker-compose.dev.yml -p bifrost-debug-$(./debug.sh status | grep -oP 'project: \K\S+' | head -1) build --no-cache worker api 2>&1 | tail -5
```

If `./debug.sh status` doesn't expose the project name in that form, run instead:
```bash
docker build -f api/Dockerfile.dev -t bifrost-shim-check api/
docker run --rm bifrost-shim-check sh -c 'which bifrost && bifrost --help | head -3'
```
Expected: `/usr/local/bin/bifrost` then the CLI help banner.

- [ ] **Step 4: Commit**

```bash
git add api/Dockerfile.dev
git commit -m "feat(worker): install bifrost CLI shim in dev image

Adds /usr/local/bin/bifrost shim that execs python -m bifrost. Workflows
running on a worker can now subprocess.run([\"bifrost\", ...]); previously
the console script wasn't on PATH because requirements.lock installs deps
only, not the api/ package itself."
```

---

## Task 2: Install the `bifrost` CLI shim in production worker image

**Files:**
- Modify: `api/Dockerfile`

**Context:** Same fix, production image. Without this, `bifrost files` won't work in any deployed environment.

- [ ] **Step 1: Locate the equivalent insertion point in `api/Dockerfile`**

Open the file and find the line `COPY --from=builder /usr/local/bin /usr/local/bin`. The shim goes immediately after it, before any user creation.

- [ ] **Step 2: Insert the shim**

```dockerfile
RUN printf '#!/bin/sh\nexec python -m bifrost "$@"\n' > /usr/local/bin/bifrost \
    && chmod +x /usr/local/bin/bifrost
```

(Identical to Task 1; no dev-vs-prod difference — the bifrost package is at `/app/bifrost/` in both images, and `PYTHONPATH=/app` is set in both.)

- [ ] **Step 3: Build and verify the prod image**

```bash
docker build -f api/Dockerfile -t bifrost-prod-shim-check .
docker run --rm bifrost-prod-shim-check sh -c 'which bifrost && bifrost --help | head -3'
```
Expected: `/usr/local/bin/bifrost` then CLI help.

- [ ] **Step 4: Commit**

```bash
git add api/Dockerfile
git commit -m "feat(worker): install bifrost CLI shim in production image

Mirrors api/Dockerfile.dev. Required for Buildfrost agent to shell out
to bifrost from a workflow in prod."
```

---

## Task 3: Add `files.search()` SDK method

**Files:**
- Modify: `api/bifrost/files.py` (append a new static method to the `files` class after `get_signed_url`, around line 298)
- Test: `api/tests/unit/test_files_sdk_search.py`

**Context:** The other five verbs (read/write/list/delete/exists) already have SDK methods in `api/bifrost/files.py:41-251`. Only `search` is missing. The endpoint exists at `api/src/routers/files.py:989` with request/response shapes in `api/src/models/contracts/editor.py:231-263`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/unit/test_files_sdk_search.py`:

```python
"""Unit test for the bifrost.files.search SDK method.

Mocks the underlying client so no network is required. The test asserts
the SDK posts to the right path with the right body and returns the
expected shape.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.files import files  # noqa: E402


_REQUEST = httpx.Request("POST", "https://bifrost.test/api/files/search")


def _fake_response(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body, request=_REQUEST)


@pytest.mark.asyncio
async def test_search_posts_to_endpoint_with_defaults() -> None:
    captured: dict = {}

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured["path"] = path
        captured["body"] = json
        return _fake_response({
            "query": "needle",
            "total_matches": 1,
            "files_searched": 1,
            "results": [
                {
                    "file_path": "a.py",
                    "line": 3,
                    "column": 0,
                    "match_text": "needle",
                    "context_before": None,
                    "context_after": None,
                }
            ],
            "truncated": False,
            "search_time_ms": 4,
        })

    client = mock.AsyncMock()
    client.post = capturing_post

    with mock.patch("bifrost.files.get_client", return_value=client):
        result = await files.search("needle")

    assert captured["path"] == "/api/files/search"
    assert captured["body"]["query"] == "needle"
    assert captured["body"]["case_sensitive"] is False
    assert captured["body"]["is_regex"] is False
    assert captured["body"]["include_pattern"] == "**/*"
    assert captured["body"]["max_results"] == 1000
    assert result["total_matches"] == 1
    assert result["results"][0]["file_path"] == "a.py"


@pytest.mark.asyncio
async def test_search_passes_through_options() -> None:
    captured: dict = {}

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured["body"] = json
        return _fake_response({
            "query": "x",
            "total_matches": 0,
            "files_searched": 0,
            "results": [],
            "truncated": False,
            "search_time_ms": 1,
        })

    client = mock.AsyncMock()
    client.post = capturing_post

    with mock.patch("bifrost.files.get_client", return_value=client):
        await files.search(
            "x",
            case_sensitive=True,
            is_regex=True,
            include_pattern="**/*.py",
            max_results=50,
        )

    assert captured["body"]["case_sensitive"] is True
    assert captured["body"]["is_regex"] is True
    assert captured["body"]["include_pattern"] == "**/*.py"
    assert captured["body"]["max_results"] == 50
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./test.sh tests/unit/test_files_sdk_search.py -v
```
Expected: FAIL with `AttributeError: type object 'files' has no attribute 'search'`.

- [ ] **Step 3: Add `files.search()` to `api/bifrost/files.py`**

Append inside the `files` class (after `get_signed_url`, before the closing of the class body):

```python
    @staticmethod
    async def search(
        query: str,
        case_sensitive: bool = False,
        is_regex: bool = False,
        include_pattern: str = "**/*",
        max_results: int = 1000,
    ) -> dict:
        """
        Search workspace file contents.

        Args:
            query: Text or regex pattern to search for.
            case_sensitive: Case-sensitive matching (default: False).
            is_regex: Treat query as a regex (default: False; literal substring).
            include_pattern: Glob restricting which files to search
                (default: ``**/*``).
            max_results: Maximum results returned (default: 1000, max: 10000).

        Returns:
            dict with keys: query, total_matches, files_searched, results,
            truncated, search_time_ms. ``results`` is a list of dicts with
            keys: file_path, line, column, match_text, context_before,
            context_after.

        Example:
            >>> from bifrost import files
            >>> hits = await files.search("TODO", include_pattern="**/*.py")
            >>> for r in hits["results"]:
            ...     print(f"{r['file_path']}:{r['line']}: {r['match_text']}")
        """
        client = get_client()
        response = await client.post(
            "/api/files/search",
            json={
                "query": query,
                "case_sensitive": case_sensitive,
                "is_regex": is_regex,
                "include_pattern": include_pattern,
                "max_results": max_results,
            },
        )
        raise_for_status_with_detail(response)
        return response.json()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./test.sh tests/unit/test_files_sdk_search.py -v
```
Expected: PASS for both tests.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/files.py api/tests/unit/test_files_sdk_search.py
git commit -m "feat(sdk): add files.search() wrapping /api/files/search

Thin async wrapper over the existing search endpoint. Returns raw dict
matching the SearchResponse contract; CLI surface will format it next."
```

---

## Task 4: Add `bifrost files` CLI sub-group with read/write/list/delete/exists/search verbs

**Files:**
- Create: `api/bifrost/commands/files.py`
- Modify: `api/bifrost/commands/__init__.py` (add `files_group` to `ENTITY_GROUPS`)
- Test: `api/tests/unit/test_cli_files.py`

**Context:** Mirrors `api/bifrost/commands/configs.py:1-80` for module shape, and `api/tests/unit/test_cli_tables.py:1-80` for test shape. The `files` SDK is async; CLI verbs use `run_async` from `base.py`. `--json` is auto-attached by `_EntityGroup.add_command` (`base.py:312`).

### Sub-step 4a: Create the command module with read/write/list/delete/exists

- [ ] **Step 1: Write failing tests for read/write/list/delete/exists**

Create `api/tests/unit/test_cli_files.py`:

```python
"""Smoke tests for ``bifrost files`` CLI commands.

Mocks BifrostClient.get_instance so no network or credentials are needed.
Mirrors the pattern in test_cli_tables.py.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
from click.testing import CliRunner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.files import files_group  # noqa: E402


_DUMMY_REQUEST = httpx.Request("POST", "https://bifrost.test/api/files/read")


def _fake_response(body: dict, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=_DUMMY_REQUEST)


def _make_mock_client(captured: dict, body_by_path: dict[str, dict]) -> mock.AsyncMock:
    """Return a mock BifrostClient that records calls and replies per path."""

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured.setdefault("calls", []).append({"path": path, "body": json})
        return _fake_response(body_by_path.get(path, {}))

    client = mock.AsyncMock()
    client.post = capturing_post
    return client


def _invoke(args: list[str], captured: dict, body_by_path: dict[str, dict]):
    client = _make_mock_client(captured, body_by_path)
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        runner = CliRunner()
        return runner.invoke(files_group, args)


class TestRead:
    def test_reads_workspace_file_by_default(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["read", "data/customers.csv"],
            captured,
            {"/api/files/read": {"content": "id,name\n1,Acme\n"}},
        )
        assert result.exit_code == 0, result.output
        assert "id,name" in result.output
        assert captured["calls"][0]["path"] == "/api/files/read"
        body = captured["calls"][0]["body"]
        assert body["path"] == "data/customers.csv"
        assert body["location"] == "workspace"
        assert body["binary"] is False

    def test_passes_location_flag(self) -> None:
        captured: dict = {}
        _invoke(
            ["read", "form_id/uuid/file.txt", "--location", "uploads"],
            captured,
            {"/api/files/read": {"content": ""}},
        )
        assert captured["calls"][0]["body"]["location"] == "uploads"


class TestWrite:
    def test_writes_with_content_flag(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--content", "hello"],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["path"] == "out.txt"
        assert body["content"] == "hello"
        assert body["binary"] is False

    def test_writes_from_stdin_when_dash(self) -> None:
        captured: dict = {}
        runner = CliRunner()
        client = _make_mock_client(captured, {"/api/files/write": {}})
        with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
            result = runner.invoke(files_group, ["write", "out.txt", "-"], input="from-stdin\n")
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"]["content"] == "from-stdin\n"

    def test_writes_from_file_flag(self, tmp_path) -> None:
        local = tmp_path / "local.txt"
        local.write_text("local-content")
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--from-file", str(local)],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"]["content"] == "local-content"

    def test_rejects_multiple_content_sources(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--content", "x", "--from-file", "/tmp/y"],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code != 0
        assert "exactly one" in result.output.lower() or "mutually exclusive" in result.output.lower()


class TestList:
    def test_list_default_directory(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["list"],
            captured,
            {"/api/files/list": {"files": ["a.txt", "b/"]}},
        )
        assert result.exit_code == 0, result.output
        assert "a.txt" in result.output
        assert captured["calls"][0]["body"]["directory"] == ""

    def test_list_with_prefix(self) -> None:
        captured: dict = {}
        _invoke(
            ["list", "uploads"],
            captured,
            {"/api/files/list": {"files": []}},
        )
        assert captured["calls"][0]["body"]["directory"] == "uploads"


class TestDelete:
    def test_delete_posts_to_endpoint(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["delete", "old.txt"],
            captured,
            {"/api/files/delete": {}},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["path"] == "old.txt"


class TestExists:
    def test_exists_true(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["exists", "x.txt"],
            captured,
            {"/api/files/exists": {"exists": True}},
        )
        assert result.exit_code == 0, result.output
        assert "true" in result.output.lower()

    def test_exists_false_exits_nonzero(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["exists", "missing.txt"],
            captured,
            {"/api/files/exists": {"exists": False}},
        )
        assert result.exit_code == 1
        assert "false" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./test.sh tests/unit/test_cli_files.py -v
```
Expected: All fail with `ModuleNotFoundError: No module named 'bifrost.commands.files'`.

- [ ] **Step 3: Create the command module**

Create `api/bifrost/commands/files.py`:

```python
"""CLI commands for managing workspace files.

Implements the ``bifrost files`` sub-group. Each verb is a thin wrapper
around an ``api/bifrost/files.py`` SDK method, which in turn calls the
matching ``/api/files/*`` HTTP endpoint.

Verbs:

* ``bifrost files read <path> [--location LOC]`` → SDK ``files.read``
* ``bifrost files write <path> (--content S | --from-file F | -) [--location LOC]``
  → SDK ``files.write``
* ``bifrost files list [directory] [--location LOC]`` → SDK ``files.list``
* ``bifrost files delete <path> [--location LOC]`` → SDK ``files.delete``
* ``bifrost files exists <path> [--location LOC]`` → SDK ``files.exists``;
  exits 0 if exists, 1 if not
* ``bifrost files search <query> [--regex] [--case-sensitive]
  [--include GLOB] [--max-results N]`` → SDK ``files.search``

There is no ``stat`` verb — the SDK only surfaces ``exists``. There is no
``mode`` flag — workers always run in cloud mode; local mode is for the
laptop CLI where the user controls cwd directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from bifrost.client import BifrostClient
from bifrost.files import files as files_sdk

from .base import entity_group, output_result, pass_resolver, run_async

files_group = entity_group("files", "Read, write, list, search workspace files.")


_LOCATION_HELP = (
    'Storage location. Reserved: "workspace" (default), "temp", "uploads". '
    "Freeform names (e.g. \"reports\") are also accepted."
)


@files_group.command("read")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def read_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Read a workspace file and write its contents to stdout."""
    content = await files_sdk.read(path, location=location)
    # Avoid output_result()'s key:value dict formatting; raw stdout is what
    # shell pipelines and agents expect from a `read` verb.
    click.echo(content, nl=False)


@files_group.command("write")
@click.argument("path")
@click.argument("source", required=False)
@click.option("--content", "content_flag", default=None, help="Inline content to write.")
@click.option("--from-file", "from_file", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Read content from a local file.")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def write_cmd(
    ctx: click.Context,
    path: str,
    source: str | None,
    content_flag: str | None,
    from_file: str | None,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Write to a workspace file. Source: --content, --from-file, or `-` for stdin."""
    sources = [s for s in (content_flag, from_file, source) if s is not None]
    if len(sources) != 1:
        raise click.UsageError(
            "Provide exactly one content source: --content, --from-file, or `-` for stdin."
        )

    if content_flag is not None:
        content = content_flag
    elif from_file is not None:
        content = Path(from_file).read_text()
    elif source == "-":
        content = sys.stdin.read()
    else:
        # Positional source other than `-` is not allowed (avoids ambiguity
        # with shell expansion accidentally passing a filename).
        raise click.UsageError(
            "Positional content must be `-` for stdin. Use --content or --from-file otherwise."
        )

    await files_sdk.write(path, content, location=location)


@files_group.command("list")
@click.argument("directory", required=False, default="")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def list_cmd(
    ctx: click.Context,
    directory: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """List files in a directory (default: location root)."""
    items = await files_sdk.list(directory=directory, location=location)
    output_result(items, ctx=ctx)


@files_group.command("delete")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def delete_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Delete a workspace file."""
    await files_sdk.delete(path, location=location)


@files_group.command("exists")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def exists_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Check if a file exists. Exits 0 if yes, 1 if no (script-friendly)."""
    found = await files_sdk.exists(path, location=location)
    output_result({"exists": found}, ctx=ctx)
    if not found:
        sys.exit(1)


@files_group.command("search")
@click.argument("query")
@click.option("--regex", "is_regex", is_flag=True, default=False, help="Treat query as a regex.")
@click.option("--case-sensitive", "case_sensitive", is_flag=True, default=False)
@click.option("--include", "include_pattern", default="**/*",
              help='Glob restricting which files to search (default: "**/*").')
@click.option("--max-results", "max_results", type=int, default=1000,
              help="Maximum results to return (default: 1000, max: 10000).")
@click.pass_context
@pass_resolver
@run_async
async def search_cmd(
    ctx: click.Context,
    query: str,
    is_regex: bool,
    case_sensitive: bool,
    include_pattern: str,
    max_results: int,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Search workspace file contents."""
    result = await files_sdk.search(
        query,
        case_sensitive=case_sensitive,
        is_regex=is_regex,
        include_pattern=include_pattern,
        max_results=max_results,
    )
    output_result(result, ctx=ctx)


__all__ = ["files_group"]
```

- [ ] **Step 4: Run unit tests to verify read/write/list/delete/exists pass**

```bash
./test.sh tests/unit/test_cli_files.py -v -k "not search"
```
Expected: All passing.

- [ ] **Step 5: Add search tests to `test_cli_files.py`**

Append to `api/tests/unit/test_cli_files.py`:

```python
class TestSearch:
    def test_search_posts_query(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["search", "TODO"],
            captured,
            {"/api/files/search": {
                "query": "TODO",
                "total_matches": 0,
                "files_searched": 0,
                "results": [],
                "truncated": False,
                "search_time_ms": 1,
            }},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["query"] == "TODO"
        assert body["is_regex"] is False
        assert body["case_sensitive"] is False
        assert body["include_pattern"] == "**/*"
        assert body["max_results"] == 1000

    def test_search_passes_through_flags(self) -> None:
        captured: dict = {}
        _invoke(
            ["search", "f.*o", "--regex", "--case-sensitive",
             "--include", "**/*.py", "--max-results", "50"],
            captured,
            {"/api/files/search": {
                "query": "f.*o",
                "total_matches": 0,
                "files_searched": 0,
                "results": [],
                "truncated": False,
                "search_time_ms": 1,
            }},
        )
        body = captured["calls"][0]["body"]
        assert body["is_regex"] is True
        assert body["case_sensitive"] is True
        assert body["include_pattern"] == "**/*.py"
        assert body["max_results"] == 50

    def test_search_json_output(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["search", "x", "--json"],
            captured,
            {"/api/files/search": {
                "query": "x",
                "total_matches": 1,
                "files_searched": 1,
                "results": [{
                    "file_path": "a.py", "line": 3, "column": 0,
                    "match_text": "x", "context_before": None, "context_after": None,
                }],
                "truncated": False,
                "search_time_ms": 2,
            }},
        )
        assert result.exit_code == 0, result.output
        assert '"total_matches": 1' in result.output
        assert '"file_path": "a.py"' in result.output
```

- [ ] **Step 6: Run all unit tests; expect all pass**

```bash
./test.sh tests/unit/test_cli_files.py -v
```
Expected: All passing.

- [ ] **Step 7: Register `files_group` in the entity dispatcher**

Open `api/bifrost/commands/__init__.py`. Add the import (alphabetical placement after `events`):

```python
from .files import files_group
```

Add the key to `ENTITY_GROUPS` (alphabetical placement after `"events": events_group,`):

```python
    "events": events_group,
    "files": files_group,
    "forms": forms_group,
```

- [ ] **Step 8: Smoke-test top-level dispatch via `python -m bifrost`**

```bash
cd api && python -m bifrost files --help 2>&1 | head -20
```
Expected: Help listing the `read`, `write`, `list`, `delete`, `exists`, `search` commands.

- [ ] **Step 9: Run the full CLI surface smoke test to catch dispatcher regressions**

```bash
./test.sh tests/unit/test_cli_surface_smoke.py -v
```
Expected: PASS (any failure is a registration error, not behaviour).

- [ ] **Step 10: Commit**

```bash
git add api/bifrost/commands/files.py api/bifrost/commands/__init__.py api/tests/unit/test_cli_files.py
git commit -m "feat(cli): add bifrost files read/write/list/delete/exists/search

Thin CLI wrappers over api/bifrost/files.py SDK methods. Backing
endpoints already exist in api/src/routers/files.py (incl. /search via
search_files_db). No new platform surface; just CLI exposure."
```

---

## Task 5: End-to-end test — CLI works inside a worker container

**Files:**
- Create: `api/tests/e2e/platform/test_cli_files.py`

**Context:** Unit tests prove the CLI parses and calls the SDK correctly; this test proves the *shipped* binary on the worker can hit the *real* API with engine credentials and round-trip a file. Without this, the Buildfrost agent's primary use case is unverified.

The e2e test stack already runs `authenticate_engine()` for worker processes (`api/src/services/execution/worker.py:134`), so `~/.bifrost/credentials.json` is populated for any process running inside the worker. The e2e harness exposes a way to exec into the worker container — follow the existing pattern in other `test_cli_*.py` e2e tests.

- [ ] **Step 1: Survey existing e2e CLI test patterns**

Run:
```bash
head -60 api/tests/e2e/platform/test_cli_agents.py
```

Note how it invokes the CLI inside the test stack. Look for `docker exec`, `subprocess`, or a test fixture that runs commands inside a container.

- [ ] **Step 2: Write the e2e test**

Create `api/tests/e2e/platform/test_cli_files.py`. The shape below assumes a `worker_exec` fixture or helper exists; if a different pattern is used (e.g. `run_cli_in_worker(...)`), adapt to match exactly what `test_cli_agents.py` uses. Do NOT introduce a new pattern.

```python
"""End-to-end test: the `bifrost files` CLI works from inside a worker.

This is the load-bearing test for the Buildfrost use case — proves a
workflow can shell out to `bifrost files <verb>` and have it authenticate
via engine credentials and round-trip a file.
"""

from __future__ import annotations

import json
import uuid

import pytest


@pytest.mark.e2e
def test_bifrost_on_path_in_worker(worker_exec) -> None:
    """`which bifrost` returns a path inside the worker container."""
    result = worker_exec(["bash", "-c", "which bifrost && bifrost --help | head -1"])
    assert result.returncode == 0, result.stderr
    assert "/bifrost" in result.stdout
    assert "bifrost" in result.stdout.lower()


@pytest.mark.e2e
def test_files_write_then_read_roundtrip(worker_exec) -> None:
    """Write a file via CLI, read it back, contents match."""
    path = f"e2e/{uuid.uuid4().hex}.txt"
    payload = "hello-from-cli"

    write = worker_exec(["bifrost", "files", "write", path, "--content", payload])
    assert write.returncode == 0, f"write failed: {write.stderr}"

    read = worker_exec(["bifrost", "files", "read", path])
    assert read.returncode == 0, f"read failed: {read.stderr}"
    assert read.stdout == payload

    # Cleanup
    delete = worker_exec(["bifrost", "files", "delete", path])
    assert delete.returncode == 0, delete.stderr


@pytest.mark.e2e
def test_files_exists_exit_codes(worker_exec) -> None:
    """exists returns 0 for present, 1 for absent."""
    path = f"e2e/{uuid.uuid4().hex}.txt"
    worker_exec(["bifrost", "files", "write", path, "--content", "x"])

    present = worker_exec(["bifrost", "files", "exists", path])
    assert present.returncode == 0

    worker_exec(["bifrost", "files", "delete", path])
    absent = worker_exec(["bifrost", "files", "exists", path])
    assert absent.returncode == 1


@pytest.mark.e2e
def test_files_search_finds_written_content(worker_exec) -> None:
    """search hits content written via the CLI (FileIndex must be populated on write)."""
    marker = f"BUILDFROST_E2E_MARKER_{uuid.uuid4().hex}"
    path = f"e2e/{uuid.uuid4().hex}.txt"
    worker_exec(["bifrost", "files", "write", path, "--content", f"line1\n{marker}\nline3\n"])

    result = worker_exec(["bifrost", "files", "search", marker, "--json"])
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)

    # If this assertion fails, FileIndex isn't updated synchronously on
    # /api/files/write. That's a finding worth surfacing in the spec doc's
    # "things to confirm during implementation" section, not silently
    # papered over — keep the assertion strict.
    assert body["total_matches"] >= 1, (
        "search did not find content written via CLI; "
        "FileIndex may be updated asynchronously"
    )
    assert any(r["file_path"].endswith(path.split("/")[-1]) for r in body["results"])

    worker_exec(["bifrost", "files", "delete", path])
```

- [ ] **Step 3: Find the correct `worker_exec` fixture name**

```bash
grep -rn "def worker_exec\|@pytest.fixture" api/tests/e2e/platform/conftest.py api/tests/e2e/conftest.py 2>/dev/null | head
```

If no `worker_exec` fixture exists, look at how `test_cli_agents.py` invokes the CLI and adapt the new test to use the same mechanism. Do not skip this step — picking the wrong fixture wastes a debug cycle.

- [ ] **Step 4: Run the e2e test**

```bash
./test.sh stack up
./test.sh e2e tests/e2e/platform/test_cli_files.py -v
```

Expected: All four tests PASS.

If `test_files_search_finds_written_content` fails because `FileIndex` is updated asynchronously, **do not weaken the test**. Stop, document the finding in the worktree (`docs/superpowers/plans/2026-05-15-files-cli-verbs.md` — add a "Findings" section at the bottom), and ask the user how to proceed. This is the kind of latent bug the spec explicitly called out.

- [ ] **Step 5: Commit**

```bash
git add api/tests/e2e/platform/test_cli_files.py
git commit -m "test(e2e): bifrost files CLI round-trips from inside worker

Smoke test for which bifrost + write/read/exists/search round-trip
against the running API using engine credentials. This is the load-
bearing test for the Buildfrost agent's shell-tool use case."
```

---

## Task 6: Verification + cleanup

- [ ] **Step 1: Run the full pre-completion verification sequence**

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
cd api && pyright
ruff check .
cd ../client && npm run generate:types
npm run tsc
npm run lint
cd ..
./test.sh stack up
./test.sh all
```

Every step must exit 0. Fix anything that fails before continuing.

- [ ] **Step 2: Manually invoke each verb from inside the worker to sanity-check ergonomics**

```bash
WORKER=$(docker ps --format '{{.Names}}' | grep debug | grep worker | head -1)
docker exec "$WORKER" bifrost files --help
docker exec "$WORKER" bifrost files write /tmp/sanity.txt --content "hi"
docker exec "$WORKER" bifrost files read /tmp/sanity.txt
docker exec "$WORKER" bifrost files exists /tmp/sanity.txt && echo "exists OK"
docker exec "$WORKER" bifrost files search "hi" --include "**/sanity.txt" --json
docker exec "$WORKER" bifrost files delete /tmp/sanity.txt
```

Each command should behave sensibly. Note any rough edges in the worktree journal but don't fix beyond the plan unless they're outright bugs.

- [ ] **Step 3: Final commit if anything was tidied; otherwise prepare PR**

```bash
git status
git log --oneline main..HEAD
```

Confirm the commit list reads cleanly: one commit per task, no fixup commits left over. If there are fixups, squash them.

- [ ] **Step 4: Open PR**

```bash
gh pr create --title "feat(cli): bifrost files verbs + worker CLI install" --body "$(cat <<'EOF'
## Summary
- Install /usr/local/bin/bifrost shim in both api/Dockerfile and api/Dockerfile.dev so workflows can shell out to the CLI from worker subprocesses (uses python -m bifrost, no new pip resolve)
- Add `bifrost files` CLI sub-group with read, write, list, delete, exists, search — thin wrappers over the existing api/bifrost/files.py SDK
- Add files.search() SDK method wrapping the existing /api/files/search endpoint

Spec: ~/Sync/Projects/buildfrost/backend-ask.md — unblocks Buildfrost agent's shell-tool surface. No new backend, no auth changes; engine token via authenticate_engine() already gives workers a usable bifrost session.

## Test plan
- [x] Unit tests cover all six verbs (parse, body, exit codes, --json)
- [x] E2E test proves CLI is on PATH in worker, round-trips write→read→delete, and search hits content written via CLI
- [x] pyright + ruff + tsc + eslint pass
- [x] `./test.sh all` green
EOF
)"
```

---

## Self-Review

### Spec coverage

Spec asks (`Sync/Projects/buildfrost/backend-ask.md`):

| Spec item | Plan task |
|---|---|
| Install bifrost CLI on worker PATH (dev) | Task 1 |
| Install bifrost CLI on worker PATH (prod) | Task 2 |
| `bifrost files read` | Task 4 sub-step 4a |
| `bifrost files write` (with `--content`/`--from-file`/`-`) | Task 4 |
| `bifrost files list` | Task 4 |
| `bifrost files delete` | Task 4 |
| `bifrost files stat` | **Not implemented** — spec says "the rest is nice-to-have"; SDK only has `exists`, so we expose `exists` only. Adding mtime/etag would require new endpoint surface, which the spec defers. |
| `bifrost files grep` (server-side) | Task 3 (SDK) + Task 4 (CLI) — exposed as `search` (matches existing endpoint naming) |
| `--json` flag on all verbs | Auto-attached by `_EntityGroup.add_command` (`base.py:312-317`); tested explicitly for `search` |
| FileIndex lag confirmation | Task 5 step 4 — test asserts search-after-write works; failure is escalation, not silent fallback |

`stat` is the one spec ask not implemented. The spec calls it "stretch" / "nice-to-have," and the SDK only exposes `exists`. Adding it would mean a new endpoint + new SDK method + new CLI verb, all out of scope for this plan.

### Placeholder scan

No "TBD" / "implement later" / "similar to Task N". Every code block is complete. Every test has actual assertions. Every shell command has a concrete expectation.

One soft spot: Task 5 step 3 ("find the correct `worker_exec` fixture name") is an investigation step rather than a hardcoded command — but that's deliberate, because the fixture name varies and faking it would cost more time than the lookup. Acceptable.

### Type consistency

- SDK method `files.search(query, case_sensitive, is_regex, include_pattern, max_results)` ↔ CLI flags `--case-sensitive`, `--regex`, `--include`, `--max-results` — names line up.
- `SearchRequest` (`api/src/models/contracts/editor.py:231`) field names match exactly what the SDK posts: `query`, `case_sensitive`, `is_regex`, `include_pattern`, `max_results`. Verified against `api/src/models/contracts/editor.py:231-239`.
- CLI verb names (`read`, `write`, `list`, `delete`, `exists`, `search`) match SDK method names exactly except `list` (Python builtin shadowed inside the `files` class; same name in CLI is fine).
- Test fixture name `worker_exec` is provisional — Task 5 step 3 forces verification before the test runs.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-files-cli-verbs.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
