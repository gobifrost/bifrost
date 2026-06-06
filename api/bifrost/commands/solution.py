"""CLI command ``bifrost solution`` (and the top-level ``bifrost deploy``).

A Solution is an installable surface (success-criteria §3). These commands are
the disconnected-install writer and are **non-interactive by contract**:
``deploy`` always applies the full bundle, so the whole create → deploy → run
loop runs headless (criterion 17).

* ``bifrost solution init`` — scaffold a ``bifrost.solution.yaml`` descriptor.
* ``bifrost solution deploy`` (alias: top-level ``bifrost deploy``) — read the
  descriptor, ensure the install exists, bundle the workspace's Python source +
  workflow manifest entries, and POST to ``/api/solutions/{id}/deploy``.

Apps/forms/agents/tables bundling joins in their sub-plans; Sub-plan 1 wires the
load-bearing workflow path.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.solution_descriptor import (
    DESCRIPTOR_FILENAME,
    is_solution_workspace,
    load_descriptor,
)

# Top-level source dirs whose .py files are installed as solution source.
_PY_SOURCE_DIRS = ("workflows", "modules", "shared")


def _noninteractive(yes: bool) -> bool:
    """deploy never prompts; this is here for parity with the sync path."""
    return yes or os.environ.get("BIFROST_NONINTERACTIVE") == "1"


@click.group(name="solution", help="Manage Solution installs (installable surfaces).")
def solution_group() -> None:
    pass


@solution_group.command(name="init", help="Scaffold a bifrost.solution.yaml descriptor.")
@click.argument("path", type=click.Path(file_okay=False), default=".")
@click.option("--slug", required=True, help="Solution slug (definition identity).")
@click.option("--name", default=None, help="Display name (defaults to slug).")
@click.option("--scope", type=click.Choice(["org", "global"]), default="org", show_default=True)
@click.option("--global-repo-access/--no-global-repo-access", default=False, show_default=True)
def init_cmd(path: str, slug: str, name: str | None, scope: str, global_repo_access: bool) -> None:
    workspace = pathlib.Path(path)
    workspace.mkdir(parents=True, exist_ok=True)
    descriptor = workspace / DESCRIPTOR_FILENAME
    if descriptor.exists():
        raise click.ClickException(f"{descriptor} already exists")
    descriptor.write_text(
        yaml.safe_dump(
            {
                "slug": slug,
                "name": name or slug,
                "scope": scope,
                "global_repo_access": global_repo_access,
            },
            sort_keys=False,
        )
    )
    click.echo(f"Wrote {descriptor}")


@solution_group.command(
    name="scaffold-app",
    help="Scaffold a standalone_v2 React app (package.json, vite, main.tsx, App.tsx).",
)
@click.argument("slug")
@click.option("--path", "path", default=None,
              help="App dir (default: apps/<slug> under the cwd).")
@click.option("--api-url", default=None,
              help="Instance URL the app resolves `bifrost` from (default: $BIFROST_API_URL).")
def scaffold_app_cmd(slug: str, path: str | None, api_url: str | None) -> None:
    """Write a working v2 app skeleton wired for the CLI-login dev loop."""
    import uuid as _uuid

    url = api_url or os.getenv("BIFROST_API_URL") or "http://localhost:8000"
    app_dir = pathlib.Path(path) if path else pathlib.Path("apps") / slug
    if app_dir.exists() and any(app_dir.iterdir()):
        raise click.ClickException(f"{app_dir} already exists and is not empty")
    for rel, content in _v2_scaffold_files(slug, url).items():
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    # Register the app in .bifrost/apps.yaml so `bifrost deploy` finds it (the
    # deployer reads this manifest). Without this the scaffold would be source
    # with no way to deploy — a papercut. Keyed by a fresh UUID (app identity).
    bifrost_dir = app_dir.parent.parent if path else pathlib.Path(".")
    manifest = bifrost_dir / ".bifrost" / "apps.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(manifest.read_text()) if manifest.is_file() else None
    data = data or {"apps": {}}
    app_id = str(_uuid.uuid4())
    rel_path = app_dir.relative_to(bifrost_dir).as_posix() if not path else str(app_dir)
    data.setdefault("apps", {})[app_id] = {
        "id": app_id,
        "slug": slug,
        "name": slug,
        "path": rel_path,
        "app_model": "standalone_v2",
    }
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))

    click.echo(f"Scaffolded standalone_v2 app at {app_dir}")
    click.echo(f"Registered it in {manifest} (id {app_id}).")
    click.echo("Next: cd into it, `cp .env.example .env`, `npm install`, `npm run dev`.")
    click.echo("Deploy with `bifrost deploy` from the solution root.")


def _v2_scaffold_files(slug: str, api_url: str) -> dict[str, str]:
    """The files for a working standalone_v2 app skeleton.

    Designed so a developer's local ``npm run dev`` works with ZERO token
    pasting: ``vite.config.ts`` reads the CLI's own ``BIFROST_API_URL`` +
    ``BIFROST_ACCESS_TOKEN`` (the ones ``bifrost login`` already wrote to .env)
    and exposes them to the app. Deployed, the platform injects
    ``window.__BIFROST_APP__`` instead; ``main.tsx`` prefers that and falls back
    to the dev env, so one source builds + runs in both places (Codex R4 DX).
    """
    pkg = {
        "name": slug,
        "private": True,
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        # `bifrost` resolves from THIS instance (same mechanism as the server
        # build) — no public-npm publish, no token pasting.
        "dependencies": {
            "bifrost": f"{api_url.rstrip('/')}/api/sdk/download",
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "react-router-dom": "^6.22.0",
            "lucide-react": "^0.400.0",
        },
        "devDependencies": {
            "@vitejs/plugin-react": "^4.2.0",
            "typescript": "^5.4.0",
            "vite": "^5.2.0",
        },
    }
    vite_config = """\
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, parse } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tokenless local dev — three sources, in order:
//   1. process env (the CLI exported BIFROST_API_URL/BIFROST_ACCESS_TOKEN), then
//   2. the nearest .env walking UP from this app dir (password-grant `login`
//      writes one), then
//   3. the CLI credential store via `bifrost auth token` — device-code login
//      stores the token in the OS keyring / ~/.bifrost/credentials.json (NOT a
//      .env), so without this the normal login path leaves `npm run dev`
//      tokenless (R7-P2-f).
// Deployed, window.__BIFROST_APP__ supplies these instead and main.tsx prefers it.
function readBifrostEnv() {
  const out = {
    url: process.env.BIFROST_API_URL || "",
    token: process.env.BIFROST_ACCESS_TOKEN || "",
  };
  let dir = process.cwd();
  while (!(out.url && out.token)) {
    const envPath = join(dir, ".env");
    if (existsSync(envPath)) {
      for (const line of readFileSync(envPath, "utf8").split("\\n")) {
        const m = line.match(/^\\s*(BIFROST_API_URL|BIFROST_ACCESS_TOKEN)\\s*=\\s*(.*)\\s*$/);
        if (m) {
          const v = m[2].replace(/^["']|["']$/g, "");
          if (m[1] === "BIFROST_API_URL" && !out.url) out.url = v;
          if (m[1] === "BIFROST_ACCESS_TOKEN" && !out.token) out.token = v;
        }
      }
    }
    const parent = dirname(dir);
    if (parent === dir || dir === parse(dir).root) break;
    dir = parent;
  }
  // Fall back to the CLI credential store (keyring / credentials.json).
  if (!out.token) {
    try {
      const args = ["auth", "token"];
      if (out.url) args.push("--url", out.url);
      const raw = execFileSync("bifrost", args, {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
      const creds = JSON.parse(raw);
      if (creds.access_token) out.token = creds.access_token;
      if (creds.api_url && !out.url) out.url = creds.api_url;
    } catch {
      // CLI absent / not logged in — leave tokenless; main.tsx surfaces the
      // unauthenticated state rather than crashing the dev server.
    }
  }
  return out;
}

export default defineConfig(({ command }) => {
  const env = readBifrostEnv();
  // SECURITY: the dev token is injected ONLY for `vite` (serve / `npm run dev`),
  // never for `vite build`. Baking BIFROST_ACCESS_TOKEN into the production
  // bundle via `define` would ship a usable credential to every app user
  // (Codex R6-P1-c). In a deployed build the token comes from
  // window.__BIFROST_APP__ at runtime (per viewer); the bundle stays tokenless.
  const define =
    command === "serve"
      ? {
          "import.meta.env.VITE_BIFROST_API_URL": JSON.stringify(env.url),
          "import.meta.env.VITE_BIFROST_TOKEN": JSON.stringify(env.token),
        }
      : {};
  return {
    plugins: [react()],
    define,
  };
});
"""
    index_html = f"""\
<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8" /><title>{slug}</title></head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""
    main_tsx = """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { BifrostProvider } from "bifrost";

import App from "./App";

// Deployed: the platform injects window.__BIFROST_APP__ (mount node, basename,
// per-viewer token, org). Local dev: fall back to the CLI's .env (via vite).
const boot = window.__BIFROST_APP__;
const mountEl = boot?.mountEl ?? document.getElementById("root")!;
const basename = boot?.basename ?? "/";
const baseUrl = boot?.baseUrl ?? import.meta.env.VITE_BIFROST_API_URL ?? window.location.origin;
const token = boot?.token ?? import.meta.env.VITE_BIFROST_TOKEN ?? "";
const orgScope = boot?.orgScope ?? null;
// This app's id, so useWorkflow scopes path refs to THIS install's workflow.
const appId = boot?.appId ?? null;

const root = createRoot(mountEl);
// Let the platform tear this root down on navigation (no leak).
boot?.registerUnmount?.(() => root.unmount());

root.render(
  <StrictMode>
    <BifrostProvider baseUrl={baseUrl} token={token} orgScope={orgScope} appId={appId} onLogout={boot?.onLogout}>
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </BifrostProvider>
  </StrictMode>,
);
"""
    app_tsx = """\
import { Routes, Route, Link } from "react-router-dom";
import { BifrostHeader, useWorkflow } from "bifrost";

function Home() {
  // Pass a workflow UUID or a portable `path::function` ref (e.g.
  // "workflows/hello.py::main"). Bare names are NOT resolvable — workflow
  // names aren't unique, so the execute endpoint 404s on them.
  const wf = useWorkflow<{ message: string }>("workflows/your_workflow.py::main");
  return (
    <main style={{ padding: 24 }}>
      <h1>Hello from your Bifrost app</h1>
      <p>
        <Link to="/about">About</Link>
      </p>
      <button onClick={() => wf.run({})} disabled={wf.loading}>
        {wf.loading ? "Running…" : "Run workflow"}
      </button>
      {wf.error && <pre style={{ color: "crimson" }}>{wf.error.message}</pre>}
      {wf.data && <pre>{JSON.stringify(wf.data, null, 2)}</pre>}
    </main>
  );
}

function About() {
  return (
    <main style={{ padding: 24 }}>
      <h1>About</h1>
      <p>This route is at /about — refresh works because the URL is real.</p>
      <Link to="/">Home</Link>
    </main>
  );
}

export default function App() {
  return (
    <>
      <BifrostHeader title="My App" />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/about" element={<About />} />
      </Routes>
    </>
  );
}
"""
    env_example = """\
# OPTIONAL. You normally DON'T need this file: `npm run dev` auto-discovers the
# token `bifrost login` wrote (env, or the nearest .env up the tree). Create a
# .env here only to override the instance URL / token for this app.
# BIFROST_API_URL=http://localhost:8000
# BIFROST_ACCESS_TOKEN=
"""
    readme = f"""\
# {slug} — a Bifrost standalone_v2 app

## Local dev (no token pasting)

You only need to be logged in with the CLI once — `npm run dev` reads the token
`bifrost login` already wrote (from the environment, or the nearest `.env` up
the directory tree). So from your logged-in solution workspace:

    npm install     # resolves `bifrost` from {api_url}
    npm run dev     # http://localhost:5173 — already authenticated

(If you run `npm run dev` somewhere the CLI's `.env` isn't reachable, copy
`.env.example` to `.env` and set the two BIFROST_* values.)

## Deploy

The platform builds the app server-side and serves it at `/apps/{slug}`:

    bifrost deploy
"""
    return {
        "package.json": json.dumps(pkg, indent=2) + "\n",
        "vite.config.ts": vite_config,
        "index.html": index_html,
        "src/main.tsx": main_tsx,
        "src/App.tsx": app_tsx,
        ".env.example": env_example,
        "README.md": readme,
    }


def _collect_python_files(workspace: pathlib.Path) -> dict[str, str]:
    """Collect installable Python source (relative path → text)."""
    files: dict[str, str] = {}
    for d in _PY_SOURCE_DIRS:
        root = workspace / d
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            rel = py.relative_to(workspace).as_posix()
            files[rel] = py.read_text(encoding="utf-8")
    return files


def _collect_workflows(workspace: pathlib.Path) -> list[dict]:
    """Read workflow entries from .bifrost/workflows.yaml (the descriptor indexes it)."""
    wf_file = workspace / ".bifrost" / "workflows.yaml"
    if not wf_file.is_file():
        return []
    data = yaml.safe_load(wf_file.read_text()) or {}
    raw = data.get("workflows", {})
    entries: list[dict] = []
    # workflows.yaml is keyed by workflow UUID; the display name is body["name"].
    # Pass the FULL body through (not a narrowed subset): the deployer's
    # _upsert_workflows consumes endpoint_enabled/public_endpoint/timeout_seconds/
    # category/tags as a full-replace, so dropping them here would silently reset
    # an exported workflow's endpoint + timeout on a disconnected redeploy (P2-e).
    # function_name/path are required by the deployer, so fail loudly if missing.
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        entries.append({
            **body,
            "id": body.get("id", key),
            "name": body.get("name") or key,
            "function_name": body["function_name"],
            "path": body["path"],
        })
    return entries


def _collect_tables(workspace: pathlib.Path) -> list[dict]:
    """Read table SCHEMA/POLICIES from .bifrost/tables.yaml (keyed by UUID).

    Only structure is deployed — row data is runtime state and never carried in
    a bundle (criterion 11).
    """
    tbl_file = workspace / ".bifrost" / "tables.yaml"
    if not tbl_file.is_file():
        return []
    data = yaml.safe_load(tbl_file.read_text()) or {}
    raw = data.get("tables", {})
    entries: list[dict] = []
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        entry = {
            "id": body.get("id", key),
            "name": body.get("name") or key,
            "description": body.get("description"),
            "schema": body.get("schema"),
        }
        if "policies" in body:
            entry["policies"] = body["policies"]
        entries.append(entry)
    return entries


def _collect_manifest_entities(workspace: pathlib.Path, filename: str, key: str) -> list[dict]:
    """Pass through inline manifest entries (forms/agents) keyed by UUID.

    The form/agent inline content (fields, system_prompt, etc.) lives in the
    manifest body; deploy stamps solution_id + scope and full-replaces.
    """
    f = workspace / ".bifrost" / filename
    if not f.is_file():
        return []
    data = yaml.safe_load(f.read_text()) or {}
    entries: list[dict] = []
    for map_key, body in (data.get(key, {}) or {}).items():
        if isinstance(body, dict):
            entries.append({**body, "id": body.get("id", map_key)})
    return entries


def _collect_forms(workspace: pathlib.Path) -> list[dict]:
    return _collect_manifest_entities(workspace, "forms.yaml", "forms")


def _collect_agents(workspace: pathlib.Path) -> list[dict]:
    return _collect_manifest_entities(workspace, "agents.yaml", "agents")


# Text source files sent inline as UTF-8 in ``src_files``. Everything else in
# the app dir (PNG/JPG/fonts, files under public/, etc.) is a real build input
# too — a Vite app commonly `import logo from './logo.png'` — so it's carried as
# base64 in ``bin_files`` rather than silently dropped (Codex P2-j/R4).
_APP_TEXT_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".css", ".html", ".json", ".svg", ".md")
# Editor/OS cruft that must never reach the build.
_APP_SKIP_NAMES = {".DS_Store", "Thumbs.db"}
# Generated / dependency dirs that must NEVER be bundled — after a dev runs
# `npm install` / `npm run dev` the app dir contains node_modules, dist, etc.;
# serializing them would upload a huge/broken bundle (Codex R5). Only real source
# + build inputs ship.
_APP_SKIP_DIRS = {
    "node_modules", "dist", "build", ".vite", ".git", ".next", ".turbo",
    "coverage", ".cache", "out",
}


def _collect_apps(workspace: pathlib.Path) -> list[dict]:
    """Read app entries from .bifrost/apps.yaml (keyed by UUID) + their source.

    Each app's source dir (``path``, e.g. ``apps/dash``) is read into
    ``src_files`` (text) + ``bin_files`` (base64 of non-text assets) so a v2 app
    that imports PNG/fonts or ships ``public/`` builds correctly server-side. The
    optional client-side prebuild fast-path is handled by the deploy command.
    """
    import base64

    apps_file = workspace / ".bifrost" / "apps.yaml"
    if not apps_file.is_file():
        return []
    data = yaml.safe_load(apps_file.read_text()) or {}
    raw = data.get("apps", {})
    entries: list[dict] = []
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        app_dir = workspace / body["path"]
        src_files: dict[str, str] = {}
        bin_files: dict[str, str] = {}
        if app_dir.is_dir():
            for f in app_dir.rglob("*"):
                if not f.is_file() or f.name in _APP_SKIP_NAMES:
                    continue
                # Never bundle local env files. A developer's `.env` /
                # `.env.local` holds BIFROST_ACCESS_TOKEN (the documented local
                # dev override) — shipping it lets the server-side Vite build
                # bake the token into the public JS, leaking it to every app
                # user (Codex R6-P1-c). The token reaches the runtime via
                # window.__BIFROST_APP__, never the bundle.
                if f.name == ".env" or f.name.startswith(".env."):
                    continue
                rel_parts = f.relative_to(app_dir).parts
                # Skip anything inside a generated/dependency dir (node_modules,
                # dist, …) — never bundle build output or deps.
                if any(p in _APP_SKIP_DIRS for p in rel_parts[:-1]):
                    continue
                rel = f.relative_to(app_dir).as_posix()
                if f.suffix in _APP_TEXT_SUFFIXES:
                    src_files[rel] = f.read_text(encoding="utf-8")
                else:
                    bin_files[rel] = base64.b64encode(f.read_bytes()).decode("ascii")
        entries.append({
            "id": body.get("id", key),
            "slug": body.get("slug") or key,
            "name": body.get("name") or key,
            "app_model": body.get("app_model", "inline_v1"),
            "dependencies": body.get("dependencies") or {},
            "access_level": body.get("access_level"),
            # Role bindings the deployer syncs into AppRole (Codex P1-d). Carry
            # both raw UUIDs and portable names; the deployer prefers names.
            "roles": body.get("roles") or [],
            "role_names": body.get("role_names"),
            "src_files": src_files,
            "bin_files": bin_files,
        })
    return entries


class _AmbiguousInstall(Exception):
    """More than one existing install matches (slug, scope); deploy can't pick."""


def _resolve_target_install(
    installs: list[dict], slug: str, scope: str, deployer_org_id: str | None
) -> str | None:
    """Resolve which existing install a disconnected deploy targets.

    Matches by (slug, scope). For ``global`` scope an install is one with
    ``organization_id is None``; for ``org`` scope, the install's
    ``organization_id`` must equal the deployer's own org (``deployer_org_id``) —
    NOT merely "any org-scoped install with this slug". Without that filter a
    developer in org-B running ``bifrost deploy`` of a slug that org-A already
    installed would full-replace org-A's install (Codex R6-P1-b). Each org's
    install of a slug is independent (success-criteria §3.4 / criterion 9), so
    the caller only ever resolves to (or creates) an install in their own org.

    Returns the install id if exactly one matches, ``None`` if none match (the
    caller creates a fresh install). Raises :class:`_AmbiguousInstall` if MORE
    THAN ONE install matches within the resolved scope — silently full-replacing
    the first would clobber the wrong install. The user must disambiguate with
    ``--solution <id>``.
    """
    matches = [
        s for s in installs
        if s.get("slug") == slug and (
            (scope == "global" and s.get("organization_id") is None)
            or (
                scope == "org"
                # Require a real deployer org BEFORE the equality: a None
                # deployer org must not match a GLOBAL install (organization_id
                # is also None), which `None == None` would otherwise allow — an
                # org-scoped deploy could then full-replace the global install
                # (R7-P1-a). No deployer org → no org-scope match → fresh install.
                and deployer_org_id is not None
                and s.get("organization_id") == deployer_org_id
            )
        )
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]["id"]
    listing = "\n".join(
        f"  --solution {m['id']}  (org={m.get('organization_id')})" for m in matches
    )
    raise _AmbiguousInstall(
        f"{len(matches)} installs of '{slug}' exist for scope '{scope}'. "
        f"Deploy would full-replace one of them — refusing to guess.\n"
        f"Re-run with an explicit target:\n{listing}"
    )


@solution_group.command(name="deploy", help="Deploy the current Solution workspace (full replace, non-interactive).")
@click.argument("path", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--solution", "solution_id", default=None, help="Target install id (override when ambiguous).")
@click.option("--yes", "-y", is_flag=True, default=False, help="Non-interactive: apply the full bundle without prompting.")
def deploy_cmd(path: str, solution_id: str | None, yes: bool) -> None:
    workspace = pathlib.Path(path).resolve()
    if not is_solution_workspace(workspace):
        raise click.ClickException(
            f"No {DESCRIPTOR_FILENAME} in {workspace} — not a Solution workspace. "
            f"Run `bifrost solution init` first."
        )
    descriptor = load_descriptor(workspace)
    _noninteractive(yes)  # deploy is always full-replace; flag kept for contract parity

    python_files = _collect_python_files(workspace)
    workflows = _collect_workflows(workspace)
    tables = _collect_tables(workspace)
    apps = _collect_apps(workspace)
    forms = _collect_forms(workspace)
    agents = _collect_agents(workspace)

    async def _run() -> int:
        client = BifrostClient.get_instance(require_auth=True)

        target_id = solution_id
        if target_id is None:
            # Resolve or create the install by (slug, scope).
            resp = await client.get("/api/solutions")
            installs = resp.json().get("solutions", []) if resp.status_code == 200 else []
            org = client.organization or {}
            deployer_org_id = org.get("id")
            try:
                target_id = _resolve_target_install(
                    installs, descriptor.slug, descriptor.scope, deployer_org_id
                )
            except _AmbiguousInstall as e:
                click.echo(str(e), err=True)
                return 1
            if target_id is None:
                create = await client.post("/api/solutions", json={
                    "slug": descriptor.slug,
                    "name": descriptor.name,
                    "scope": descriptor.scope,
                    "global_repo_access": descriptor.global_repo_access,
                    "git_connected": descriptor.git_connected,
                    "git_repo_url": descriptor.git_repo_url,
                })
                if create.status_code not in (200, 201):
                    click.echo(f"Failed to create install: {create.status_code} {create.text}", err=True)
                    return 1
                target_id = create.json()["id"]

        # Vendor referenced _repo/ shared modules into the bundle so the deployed
        # Solution is self-contained (criterion 5). When global_repo_access is on
        # the install can reach _repo/ at runtime, so vendoring is skipped.
        bundle_python = python_files
        if not descriptor.global_repo_access:
            from bifrost.solution_vendoring import vendor_shared_deps

            async def _repo_read(path: str) -> str | None:
                resp = await client.post("/api/files/read", json={
                    "path": path, "location": "workspace", "mode": "cloud",
                })
                if resp.status_code != 200:
                    return None
                return resp.json().get("content")

            vendored = await vendor_shared_deps(python_files, _repo_read)
            if vendored:
                click.echo(f"Vendored {len(vendored)} shared dependency file(s).")
                bundle_python = {**python_files, **vendored}

        deploy = await client.post(f"/api/solutions/{target_id}/deploy", json={
            "python_files": bundle_python,
            "workflows": workflows,
            "tables": tables,
            "apps": apps,
            "forms": forms,
            "agents": agents,
        })
        if deploy.status_code not in (200, 201):
            click.echo(f"Deploy failed: {deploy.status_code} {deploy.text}", err=True)
            return 1
        body = deploy.json()
        click.echo(
            f"Deployed install {target_id}: "
            f"{body.get('workflows_upserted', 0)} workflow(s) upserted, "
            f"{body.get('workflows_deleted', 0)} deleted."
        )
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)


def handle_solution(args: list[str]) -> int:
    """Dispatch ``bifrost solution ...`` from :func:`bifrost.cli.main`."""
    try:
        solution_group.main(args=args, standalone_mode=False, prog_name="bifrost solution")
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


def handle_deploy(args: list[str]) -> int:
    """Dispatch the top-level ``bifrost deploy`` (alias of ``solution deploy``)."""
    try:
        deploy_cmd.main(args=args, standalone_mode=False, prog_name="bifrost deploy")
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


__all__ = ["solution_group", "handle_solution", "handle_deploy"]
