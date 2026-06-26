# Solution-Bound Development Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Solution workspaces usable only after they are bound to a concrete remote Solution install, so local development cannot silently write tables, files, configs, or app calls into loose `_repo` / org / global scope.

**Architecture:** Introduce a small CLI binding layer that stores `BIFROST_SOLUTION_*` values in the workspace `.env`. `solution init` becomes a transparent alias of the new online `solution create` flow, `solution bind` attaches cloned workspaces to existing installs, and `solution start` / `solution deploy` require a bound install or an explicit `--solution`. The local dev proxy carries the bound `solution_id` explicitly because a local manifest app id may not exist in the database before the first deploy.

**Tech Stack:** Python Click CLI, existing `BifrostClient`, FastAPI auth context via `?solution=`, pytest through `./test.sh`.

---

## Model Change

The command model after this change:

- `bifrost solution init <path> --slug ...` is a transparent alias of `bifrost solution create`.
- `bifrost solution create <path> --slug ... [--org ...|--global]` creates the local descriptor and an empty remote install, then writes `.env`.
- `bifrost solution bind --solution <id-or-slug>` binds an existing local workspace to an existing remote install, then writes `.env`.
- `bifrost solution start [--solution <id-or-slug>]` requires a bound install. It has no `--org`; install scope is authoritative.
- `bifrost solution deploy [--solution <id-or-slug>]` deploys to the bound install. It no longer silently creates or guesses an install.
- `bifrost solution install` keeps its current install-from-zip behavior because it is intentionally a remote install operation.

The `.env` binding keys:

```dotenv
BIFROST_SOLUTION_ID=<install uuid>
BIFROST_SOLUTION_SLUG=<descriptor/install slug>
BIFROST_SOLUTION_ORG_ID=<org uuid or empty for global>
BIFROST_SOLUTION_SCOPE=org|global
```

`BIFROST_API_URL` already belongs in `.env` through login. Binding should not overwrite unrelated `.env` keys.

## Files

- Create `api/bifrost/solution_binding.py`: parse/write `.env` binding keys, resolve explicit install refs, validate descriptor/install matches.
- Modify `api/bifrost/commands/solution.py`: add `create`, convert `init`, add `bind`, require bindings in `start` and `deploy`, thread `solution_id` into proxy config.
- Modify `api/bifrost/solution_dev/proxy.py`: add `solution_id` to `DevProxyConfig`, append `?solution=<id>` to proxied data-plane requests, and pass `solution_id` on proxied workflow executions.
- Test `api/tests/unit/test_solution_binding.py`: `.env` merge/read/write and install validation.
- Test `api/tests/unit/test_solution_dev_command.py`: `start` binding requirements and `--solution` override.
- Test `api/tests/unit/test_cli_solution_deploy_phases.py` or new `api/tests/unit/test_cli_solution_bound_deploy.py`: deploy requires binding and uses bound install.
- Test `api/tests/unit/test_solution_dev_proxy.py`: proxied local app calls carry the bound `solution` query.

---

### Task 1: Add Solution Binding Helpers

**Files:**
- Create: `api/bifrost/solution_binding.py`
- Test: `api/tests/unit/test_solution_binding.py`

- [ ] **Step 1: Write failing tests for `.env` binding read/write**

Create `api/tests/unit/test_solution_binding.py` with these tests:

```python
from pathlib import Path

from bifrost.solution_binding import (
    SolutionBinding,
    read_solution_binding,
    write_solution_binding,
)


def test_write_solution_binding_merges_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("BIFROST_API_URL=http://api\nOTHER=value\n")

    write_solution_binding(
        tmp_path,
        SolutionBinding(
            solution_id="11111111-1111-1111-1111-111111111111",
            slug="dispatch",
            organization_id="22222222-2222-2222-2222-222222222222",
            scope="org",
        ),
    )

    text = env.read_text()
    assert "BIFROST_API_URL=http://api\n" in text
    assert "OTHER=value\n" in text
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n" in text
    assert "BIFROST_SOLUTION_SLUG=dispatch\n" in text
    assert "BIFROST_SOLUTION_ORG_ID=22222222-2222-2222-2222-222222222222\n" in text
    assert "BIFROST_SOLUTION_SCOPE=org\n" in text


def test_read_solution_binding_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_solution_binding(tmp_path) is None


def test_read_solution_binding_parses_global_scope(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=dispatch\n"
        "BIFROST_SOLUTION_ORG_ID=\n"
        "BIFROST_SOLUTION_SCOPE=global\n"
    )

    binding = read_solution_binding(tmp_path)

    assert binding is not None
    assert binding.solution_id == "11111111-1111-1111-1111-111111111111"
    assert binding.slug == "dispatch"
    assert binding.organization_id is None
    assert binding.scope == "global"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
./test.sh tests/unit/test_solution_binding.py -v
```

Expected: import failure for `bifrost.solution_binding`.

- [ ] **Step 3: Implement binding helpers**

Create `api/bifrost/solution_binding.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SOLUTION_ENV_KEYS = {
    "BIFROST_SOLUTION_ID",
    "BIFROST_SOLUTION_SLUG",
    "BIFROST_SOLUTION_ORG_ID",
    "BIFROST_SOLUTION_SCOPE",
}


@dataclass(frozen=True)
class SolutionBinding:
    solution_id: str
    slug: str
    organization_id: str | None
    scope: Literal["org", "global"]


def _env_path(workspace: Path) -> Path:
    return workspace / ".env"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :]
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    return key, value


def read_solution_binding(workspace: Path) -> SolutionBinding | None:
    env = _env_path(workspace)
    if not env.is_file():
        return None
    values: dict[str, str] = {}
    for line in env.read_text().splitlines():
        parsed = _parse_env_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    solution_id = values.get("BIFROST_SOLUTION_ID")
    slug = values.get("BIFROST_SOLUTION_SLUG")
    if not solution_id or not slug:
        return None
    scope = values.get("BIFROST_SOLUTION_SCOPE") or "org"
    if scope not in {"org", "global"}:
        return None
    org_id = values.get("BIFROST_SOLUTION_ORG_ID") or None
    if scope == "global":
        org_id = None
    return SolutionBinding(
        solution_id=solution_id,
        slug=slug,
        organization_id=org_id,
        scope=scope,  # type: ignore[arg-type]
    )


def write_solution_binding(workspace: Path, binding: SolutionBinding) -> None:
    env = _env_path(workspace)
    existing = env.read_text().splitlines() if env.is_file() else []
    kept = []
    for line in existing:
        parsed = _parse_env_line(line)
        if parsed is None or parsed[0] not in SOLUTION_ENV_KEYS:
            kept.append(line)
    additions = [
        f"BIFROST_SOLUTION_ID={binding.solution_id}",
        f"BIFROST_SOLUTION_SLUG={binding.slug}",
        f"BIFROST_SOLUTION_ORG_ID={binding.organization_id or ''}",
        f"BIFROST_SOLUTION_SCOPE={binding.scope}",
    ]
    env.write_text("\n".join([*kept, *additions]).rstrip() + "\n")
```

- [ ] **Step 4: Run binding tests**

Run:

```bash
./test.sh tests/unit/test_solution_binding.py -v
```

Expected: all tests pass.

---

### Task 2: Add Install Resolution and Validation Helpers

**Files:**
- Modify: `api/bifrost/solution_binding.py`
- Test: `api/tests/unit/test_solution_binding.py`

- [ ] **Step 1: Add failing tests for install ref resolution**

Append:

```python
import pytest

from bifrost.solution_binding import (
    SolutionBindingError,
    binding_from_install,
    resolve_install_ref,
)


def test_binding_from_install_rejects_slug_mismatch() -> None:
    with pytest.raises(SolutionBindingError, match="does not match descriptor slug"):
        binding_from_install(
            {"id": "i", "slug": "other", "organization_id": None},
            descriptor_slug="expected",
        )


def test_binding_from_install_global() -> None:
    binding = binding_from_install(
        {"id": "i", "slug": "expected", "organization_id": None},
        descriptor_slug="expected",
    )
    assert binding.scope == "global"
    assert binding.organization_id is None


def test_resolve_install_ref_rejects_ambiguous_slug() -> None:
    installs = [
        {"id": "a", "slug": "expected", "organization_id": "org-a"},
        {"id": "b", "slug": "expected", "organization_id": "org-b"},
    ]
    with pytest.raises(SolutionBindingError, match="multiple installs"):
        resolve_install_ref(installs, "expected", descriptor_slug="expected")
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
./test.sh tests/unit/test_solution_binding.py -v
```

Expected: import failure for the new helper names.

- [ ] **Step 3: Implement resolver helpers**

Add to `api/bifrost/solution_binding.py`:

```python
class SolutionBindingError(ValueError):
    pass


def binding_from_install(install: dict, *, descriptor_slug: str) -> SolutionBinding:
    slug = str(install.get("slug") or "")
    if slug != descriptor_slug:
        raise SolutionBindingError(
            f"Install slug {slug!r} does not match descriptor slug {descriptor_slug!r}"
        )
    org_id = install.get("organization_id")
    return SolutionBinding(
        solution_id=str(install["id"]),
        slug=slug,
        organization_id=str(org_id) if org_id else None,
        scope="org" if org_id else "global",
    )


def resolve_install_ref(
    installs: list[dict],
    ref: str,
    *,
    descriptor_slug: str,
) -> SolutionBinding:
    matches = [s for s in installs if s.get("id") == ref]
    if not matches:
        matches = [s for s in installs if s.get("slug") == ref]
    if not matches:
        raise SolutionBindingError(f"No solution install found for {ref!r}")
    if len(matches) > 1:
        ids = ", ".join(str(m.get("id")) for m in matches)
        raise SolutionBindingError(
            f"Slug {ref!r} matches multiple installs ({ids}); pass an install id"
        )
    return binding_from_install(matches[0], descriptor_slug=descriptor_slug)
```

- [ ] **Step 4: Run tests**

Run:

```bash
./test.sh tests/unit/test_solution_binding.py -v
```

Expected: all tests pass.

---

### Task 3: Add Online `solution create` and Make `init` an Alias

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_dev_command.py`

- [ ] **Step 1: Add failing CLI tests**

Append to `api/tests/unit/test_solution_dev_command.py`:

```python
def test_solution_init_creates_remote_install_and_env(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)

    created_payloads = []

    class _Resp:
        status_code = 201
        text = ""

        def json(self):
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "slug": "dispatch",
                "organization_id": "22222222-2222-2222-2222-222222222222",
            }

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            assert path == "/api/solutions"
            created_payloads.append(json)
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["init", ".", "--slug", "dispatch", "--name", "Dispatch"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bifrost.solution.yaml").is_file()
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111" in env
    assert created_payloads[0]["slug"] == "dispatch"
    assert created_payloads[0]["organization_id"] == "22222222-2222-2222-2222-222222222222"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
./test.sh tests/unit/test_solution_dev_command.py::test_solution_init_creates_remote_install_and_env -v
```

Expected: `.env` is missing because current `init` only writes the descriptor.

- [ ] **Step 3: Implement shared create flow**

In `api/bifrost/commands/solution.py`:

- Import `SolutionBinding`, `binding_from_install`, and `write_solution_binding`.
- Move the existing descriptor-writing body into `_write_solution_descriptor`.
- Add `_create_solution_workspace_and_install(...)`.
- Add `@solution_group.command(name="create", ...)`.
- Change `init_cmd` to call the same helper.

The create helper must:

```python
async def _create_install_for_descriptor(client, descriptor, target_org_id):
    create = await client.post("/api/solutions", json={
        "slug": descriptor.slug,
        "name": descriptor.name,
        "organization_id": target_org_id,
        "global_repo_access": descriptor.global_repo_access,
        "git_connected": descriptor.git_connected,
        "git_repo_url": descriptor.git_repo_url,
        "repo_subpath": descriptor.repo_subpath,
        "git_ref": descriptor.git_ref,
    })
    if create.status_code not in (200, 201):
        raise click.ClickException(
            f"Failed to create install: {create.status_code} {create.text}"
        )
    return create.json()
```

`init` and `create` should both print:

```text
Created Solution install <id>.
Bound workspace in .env.
```

- [ ] **Step 4: Run create/init tests**

Run:

```bash
./test.sh tests/unit/test_solution_dev_command.py::test_solution_init_creates_remote_install_and_env -v
```

Expected: pass.

---

### Task 4: Add `solution bind`

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_dev_command.py`

- [ ] **Step 1: Add failing bind test**

Append:

```python
def test_solution_bind_writes_existing_install_to_env(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: dispatch\nname: Dispatch\n")

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "solutions": [{
                    "id": "11111111-1111-1111-1111-111111111111",
                    "slug": "dispatch",
                    "organization_id": None,
                }]
            }

    class _FakeClient:
        async def get(self, path, **kwargs):
            assert path == "/api/solutions"
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["bind", "--solution", "dispatch"],
    )

    assert result.exit_code == 0, result.output
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111" in env
    assert "BIFROST_SOLUTION_SCOPE=global" in env
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
./test.sh tests/unit/test_solution_dev_command.py::test_solution_bind_writes_existing_install_to_env -v
```

Expected: Click reports no `bind` command.

- [ ] **Step 3: Implement `bind` command**

Add:

```python
@solution_group.command(name="bind", help="Bind this workspace to an existing Solution install.")
@click.option("--solution", "solution_ref", required=True, help="Solution install id or unique slug.")
def bind_cmd(solution_ref: str) -> None:
    workspace = pathlib.Path(".").resolve()
    if not is_solution_workspace(workspace):
        raise click.ClickException(
            f"No {DESCRIPTOR_FILENAME} in {workspace} — not a Solution workspace. "
            "Run `bifrost solution create` first."
        )
    descriptor = load_descriptor(workspace)

    async def _run() -> int:
        from bifrost.solution_binding import (
            SolutionBindingError,
            resolve_install_ref,
            write_solution_binding,
        )

        client = BifrostClient.get_instance(require_auth=True)
        resp = await client.get("/api/solutions")
        if resp.status_code != 200:
            raise click.ClickException(
                f"Failed to list installs ({resp.status_code}): {resp.text[:200]}"
            )
        try:
            binding = resolve_install_ref(
                resp.json().get("solutions", []),
                solution_ref,
                descriptor_slug=descriptor.slug,
            )
        except SolutionBindingError as exc:
            raise click.ClickException(str(exc))
        write_solution_binding(workspace, binding)
        click.echo(f"Bound workspace to Solution install {binding.solution_id}.")
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)
```

- [ ] **Step 4: Run bind test**

Run:

```bash
./test.sh tests/unit/test_solution_dev_command.py::test_solution_bind_writes_existing_install_to_env -v
```

Expected: pass.

---

### Task 5: Require Binding in `solution start`

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_dev_command.py`

- [ ] **Step 1: Add failing start tests**

Modify the existing `test_start_spawns_npm_via_resolved_path` setup to write `.env` with `BIFROST_SOLUTION_ID`, or add this focused test:

```python
def test_start_refuses_solution_workspace_without_binding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\n")
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {
            "a": {"id": "a", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        }})
    )
    (tmp_path / "apps" / "dash").mkdir(parents=True)

    result = CliRunner().invoke(solution_group, ["start"])

    assert result.exit_code != 0
    assert "not bound to a Solution install" in result.output
    assert "bifrost solution create" in result.output
    assert "bifrost solution bind" in result.output
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
./test.sh tests/unit/test_solution_dev_command.py::test_start_refuses_solution_workspace_without_binding -v
```

Expected: current code tries to authenticate/select app instead of refusing for missing binding.

- [ ] **Step 3: Implement start binding resolution**

Change `start_cmd` signature to:

```python
@solution_group.command(name="start", help="Run the app's dev server + local workflows (one origin).")
@click.argument("app_slug", required=False)
@click.option("--solution", "solution_ref", default=None, help="Solution install id or unique slug; also updates .env binding.")
@click.option("--port", default=3000, show_default=True, type=int, help="Local origin port.")
def start_cmd(app_slug: str | None, solution_ref: str | None, port: int) -> None:
```

Remove `@org_option` from `start`.

Resolution order:

1. If `--solution` is passed, list installs, validate against descriptor slug, write `.env`, and use it.
2. Else read `.env` with `read_solution_binding`.
3. If missing, raise:

```text
This Solution workspace is not bound to a Solution install.
Run `bifrost solution create` for a new install, or `bifrost solution bind --solution <id-or-slug>` for an existing one.
```

Use the binding’s `organization_id` for `/api/sdk/context`, not a user-supplied `--org`.

- [ ] **Step 4: Update existing start tests**

In tests that expect `solution start` to proceed, write:

```python
(tmp_path / ".env").write_text(
    "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
    "BIFROST_SOLUTION_SLUG=s\n"
    "BIFROST_SOLUTION_ORG_ID=org-1\n"
    "BIFROST_SOLUTION_SCOPE=org\n"
)
```

Expected: existing tests continue past the binding gate.

- [ ] **Step 5: Run start tests**

Run:

```bash
./test.sh tests/unit/test_solution_dev_command.py -v
```

Expected: all solution dev command tests pass.

---

### Task 6: Thread Bound `solution_id` Through the Local Dev Proxy

**Files:**
- Modify: `api/bifrost/solution_dev/proxy.py`
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_dev_proxy.py`

- [ ] **Step 1: Add failing proxy test**

Add to `api/tests/unit/test_solution_dev_proxy.py`:

```python
def test_dev_proxy_appends_solution_query_to_api_requests():
    import yarl
    from bifrost.solution_dev.proxy import _join_upstream_with_solution

    target = _join_upstream_with_solution(
        "http://api.local",
        yarl.URL("/api/files/list?location=reports"),
        solution_id="11111111-1111-1111-1111-111111111111",
    )

    assert target == (
        "http://api.local/api/files/list?"
        "location=reports&solution=11111111-1111-1111-1111-111111111111"
    )
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
./test.sh tests/unit/test_solution_dev_proxy.py::test_dev_proxy_appends_solution_query_to_api_requests -v
```

Expected: helper missing.

- [ ] **Step 3: Implement proxy solution plumbing**

In `api/bifrost/solution_dev/proxy.py`:

- Add `solution_id: str` to `DevProxyConfig`.
- Add `_join_upstream_with_solution(base_url, rel_url, solution_id)`.
- Use it in `_api_proxy_handler` for `/api/*` requests.
- In `_execute_handler`, when proxying to the remote API, send:

```python
proxied_body = dict(body)
proxied_body.setdefault("solution_id", cfg.solution_id)
```

The helper should preserve existing query params and not duplicate `solution`:

```python
def _join_upstream_with_solution(base_url: str, rel_url: yarl.URL, *, solution_id: str) -> str:
    target = yarl.URL(_join_upstream(base_url, rel_url))
    if "solution" in target.query:
        return str(target)
    return str(target.update_query({**target.query, "solution": solution_id}))
```

In `api/bifrost/commands/solution.py`, pass `binding.solution_id` into `DevProxyConfig`.

- [ ] **Step 4: Run proxy tests**

Run:

```bash
./test.sh tests/unit/test_solution_dev_proxy.py -v
```

Expected: pass.

---

### Task 7: Require Binding in `solution deploy`

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_cli_solution_bound_deploy.py`

- [ ] **Step 1: Add deploy binding tests**

Create `api/tests/unit/test_cli_solution_bound_deploy.py`:

```python
from pathlib import Path

from click.testing import CliRunner

from bifrost.commands.solution import solution_group


def test_deploy_refuses_without_binding(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\n")

    result = CliRunner().invoke(solution_group, ["deploy"])

    assert result.exit_code != 0
    assert "not bound to a Solution install" in result.output


def test_deploy_accepts_solution_override_without_env(tmp_path: Path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\n")
    (tmp_path / ".bifrost").mkdir()

    calls = []

    class _ListResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "solutions": [{
                    "id": "11111111-1111-1111-1111-111111111111",
                    "slug": "s",
                    "organization_id": None,
                }]
            }

    class _DeployResp:
        status_code = 202
        text = ""

        def json(self):
            return {"deploy_job_id": "job-1"}

    class _JobResp:
        status_code = 200

        def json(self):
            return {"status": "succeeded"}

    class _FakeClient:
        async def get(self, path, **kwargs):
            if path == "/api/solutions":
                return _ListResp()
            return _JobResp()

        async def post(self, path, **kwargs):
            calls.append(path)
            return _DeployResp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["deploy", "--solution", "11111111-1111-1111-1111-111111111111"],
    )

    assert result.exit_code == 0, result.output
    assert "/api/solutions/11111111-1111-1111-1111-111111111111/deploy" in calls
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
./test.sh tests/unit/test_cli_solution_bound_deploy.py -v
```

Expected: current deploy creates/resolves instead of refusing without binding.

- [ ] **Step 3: Implement deploy binding resolution**

Change `deploy_cmd`:

- Add `--solution`.
- Remove `@org_option`.
- Before scanning/deploying, resolve binding by `--solution` or `.env`.
- Delete the create-if-missing branch from deploy.
- Use `target_id = binding.solution_id`.
- On successful deploy, rewrite `.env` with the binding.

Do not change `solution install` in this task.

- [ ] **Step 4: Run deploy binding tests**

Run:

```bash
./test.sh tests/unit/test_cli_solution_bound_deploy.py -v
```

Expected: pass.

---

### Task 8: Update User-Facing Messages and Docs

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Modify: `docs/llm.txt` if it documents Solution first-run flow
- Modify: `api/tests/unit/test_solution_workspace_guard.py`

- [ ] **Step 1: Update guard/error text tests**

Search for current messages:

```bash
rg -n "solution init|solution deploy|Solution workspace|not bound" api/tests docs api/bifrost
```

Update tests to expect:

```text
Run `bifrost solution create` for a new install, or `bifrost solution bind --solution <id-or-slug>` for an existing one.
```

- [ ] **Step 2: Update command help text**

Ensure `bifrost solution --help` describes:

```text
create   Create a local Solution workspace and empty remote install.
init     Alias for create.
bind     Bind this workspace to an existing install.
start    Run the bound install locally.
deploy   Deploy to the bound install.
```

- [ ] **Step 3: Update docs**

If `docs/llm.txt` exists and includes Solution first-run recipes, update the recipe to:

```bash
bifrost solution create . --slug example --name "Example"
bifrost solution scaffold-app dashboard
bifrost solution start dashboard
bifrost solution deploy
```

Existing-clone recipe:

```bash
bifrost solution bind --solution <id-or-slug>
bifrost solution start
```

- [ ] **Step 4: Run targeted docs/help-adjacent tests**

Run:

```bash
./test.sh tests/unit/test_solution_workspace_guard.py tests/unit/test_solution_dev_command.py -v
```

Expected: pass.

---

### Task 9: Verification

**Files:**
- No new code changes.

- [ ] **Step 1: Run targeted unit tests**

Run:

```bash
./test.sh tests/unit/test_solution_binding.py \
  tests/unit/test_solution_dev_command.py \
  tests/unit/test_solution_dev_proxy.py \
  tests/unit/test_cli_solution_bound_deploy.py \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run broader Solution CLI tests**

Run:

```bash
./test.sh tests/unit/test_cli_solution_deploy_phases.py \
  tests/unit/test_cli_solution_run.py \
  tests/unit/test_solution_workspace_guard.py \
  tests/unit/test_solution_descriptor.py \
  -v
```

Expected: all pass or failures are unrelated pre-existing expectations that need updating for the model change.

- [ ] **Step 3: Run lint/type checks for touched Python**

Run:

```bash
cd api && ruff check bifrost/commands/solution.py bifrost/solution_binding.py bifrost/solution_dev/proxy.py tests/unit/test_solution_binding.py tests/unit/test_solution_dev_command.py tests/unit/test_solution_dev_proxy.py
```

Expected: no ruff violations.

- [ ] **Step 4: Commit**

Run:

```bash
git add api/bifrost/solution_binding.py \
  api/bifrost/commands/solution.py \
  api/bifrost/solution_dev/proxy.py \
  api/tests/unit/test_solution_binding.py \
  api/tests/unit/test_solution_dev_command.py \
  api/tests/unit/test_solution_dev_proxy.py \
  api/tests/unit/test_cli_solution_bound_deploy.py \
  docs/llm.txt
git commit -m "feat: require solution-bound local development"
```

If `docs/llm.txt` was not touched, omit it from `git add`.

---

## Self-Review

- Spec coverage: The plan covers stamped `.env` binding, online `init/create`, existing-install `bind`, no `--org` on `start`, binding-required `start` and `deploy`, and explicit proxy `solution_id` propagation for pre-deploy app calls.
- Placeholder scan: No `TBD`, deferred implementation, or unnamed tests remain.
- Type consistency: `SolutionBinding.solution_id`, `slug`, `organization_id`, and `scope` are used consistently across helper, command, and proxy tasks.
