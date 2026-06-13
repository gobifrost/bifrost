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
import subprocess

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.solution_descriptor import (
    DESCRIPTOR_FILENAME,
    find_solution_root,
    is_solution_workspace,
    load_descriptor,
)

# The scaffold's sample workflow. It lives at the SOLUTION ROOT (not under the
# app dir) so its ``path::fn`` ref resolves the same way everywhere: workflow
# refs are workspace-root-relative, so the app's ``functions/hello.py::main``
# means ``<solution-root>/functions/hello.py``. ``bifrost solution start``
# discovers it from the root and runs it locally, so the scaffold's button works
# on first run with no deploy.
_SAMPLE_WORKFLOW_PATH = "functions/hello.py"
_SAMPLE_WORKFLOW_REF = f"{_SAMPLE_WORKFLOW_PATH}::main"
_SAMPLE_WORKFLOW_SOURCE = '''\
from bifrost import workflow


@workflow
async def main():
    """The scaffold's sample function — `bifrost solution start` runs this
    locally so the app's first-run button works with no deploy."""
    return {"message": "Hello from your Bifrost solution"}
'''


@click.group(name="solution", help="Manage Solution installs (installable surfaces).")
def solution_group() -> None:
    pass


@solution_group.command(name="init", help="Scaffold a bifrost.solution.yaml descriptor.")
@click.argument("path", type=click.Path(file_okay=False), default=".")
@click.option("--slug", required=True, help="Solution slug (definition identity).")
@click.option("--name", default=None, help="Display name (defaults to slug).")
@click.option("--version", "version", default="0.1.0", show_default=True,
              help="Bundle version recorded on the install at deploy time.")
@click.option("--scope", type=click.Choice(["org", "global"]), default="org", show_default=True)
@click.option("--global-repo-access/--no-global-repo-access", default=False, show_default=True)
def init_cmd(
    path: str, slug: str, name: str | None, version: str, scope: str, global_repo_access: bool
) -> None:
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
                "version": version,
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
              help="App dir inside the solution workspace (default: apps/<slug> under the solution root).")
@click.option("--api-url", default=None,
              help="Instance URL the app resolves `bifrost` from (default: $BIFROST_API_URL).")
def scaffold_app_cmd(slug: str, path: str | None, api_url: str | None) -> None:
    """Write a working v2 app skeleton wired for the CLI-login dev loop."""
    import uuid as _uuid

    url = api_url or os.getenv("BIFROST_API_URL") or "http://localhost:8000"

    # Anchor everything at the SOLUTION ROOT (the dir holding the descriptor),
    # found by walking up from cwd. Guessing the root from the app dir
    # (app_dir.parent.parent) wrote the .bifrost/ manifests OUTSIDE the real
    # root for nested --path values — deploy never saw them.
    root = find_solution_root(pathlib.Path.cwd())
    if root is None:
        raise click.ClickException(
            "Not inside a solution workspace (no solution descriptor found). "
            "Run this from your solution root (created by `bifrost solution init`)."
        )

    app_dir = (pathlib.Path(path) if path else root / "apps" / slug).resolve()
    try:
        # POSIX root-relative: _app_source_dirs compares manifest paths with
        # POSIX separators, so an OS-separator or cwd-relative path here makes
        # the app's .py files double-collect as workflow source on Windows.
        rel_path = app_dir.relative_to(root).as_posix()
    except ValueError:
        raise click.ClickException(f"--path must point inside the solution workspace ({root})")

    if app_dir.exists() and any(app_dir.iterdir()):
        raise click.ClickException(f"{app_dir} already exists and is not empty")
    for rel, content in _v2_scaffold_files(slug, url).items():
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    # Register the app in .bifrost/apps.yaml so `bifrost deploy` finds it (the
    # deployer reads this manifest). Without this the scaffold would be source
    # with no way to deploy — a papercut. Keyed by a fresh UUID (app identity).

    # Write the sample workflow at the SOLUTION ROOT (not under the app dir), so
    # its ``path::fn`` ref (``functions/hello.py::main``) resolves the same way
    # everywhere — refs are workspace-root-relative. ``solution start`` discovers
    # it from the root and runs the app's first-run button locally. Don't clobber
    # an existing file (a re-scaffold of a second app must not overwrite edits).
    sample_dest = root / _SAMPLE_WORKFLOW_PATH
    if not sample_dest.exists():
        sample_dest.parent.mkdir(parents=True, exist_ok=True)
        sample_dest.write_text(_SAMPLE_WORKFLOW_SOURCE)
        # Index the sample in .bifrost/workflows.yaml so `bifrost deploy` creates
        # a Workflow ROW for it — without this, deploy bundles the source but the
        # app's `functions/hello.py::main` ref 404s on a deployed install (the
        # source has no row to resolve). Keyed by a fresh UUID (workflow identity).
        wf_manifest = root / ".bifrost" / "workflows.yaml"
        wf_manifest.parent.mkdir(parents=True, exist_ok=True)
        wf_data = yaml.safe_load(wf_manifest.read_text()) if wf_manifest.is_file() else None
        wf_data = wf_data or {"workflows": {}}
        wf_id = str(_uuid.uuid4())
        wf_data.setdefault("workflows", {})[wf_id] = {
            "id": wf_id,
            "name": "hello",
            "path": _SAMPLE_WORKFLOW_PATH,
            "function_name": "main",
        }
        wf_manifest.write_text(yaml.safe_dump(wf_data, sort_keys=False))

    manifest = root / ".bifrost" / "apps.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(manifest.read_text()) if manifest.is_file() else None
    data = data or {"apps": {}}
    app_id = str(_uuid.uuid4())
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
    if sample_dest.exists():
        click.echo(f"Sample workflow at {sample_dest} (ref {_SAMPLE_WORKFLOW_REF}).")
    click.echo("Next: run `bifrost solution start` from the solution root — it serves the")
    click.echo("app and runs your local workflows behind one origin (no deploy needed).")
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
          "import.meta.env.VITE_BIFROST_APP_ID": JSON.stringify(process.env.VITE_BIFROST_APP_ID || ""),
          "import.meta.env.VITE_BIFROST_ORG_ID": JSON.stringify(process.env.VITE_BIFROST_ORG_ID || ""),
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

// Deployed: the platform injects this app's bootstrap (mount node, basename,
// per-viewer token, org). It keys the bootstrap by THIS entry's `m` nonce in a
// registry, so a fast navigation between two apps can't make our still-loading
// entry read the OTHER app's bootstrap (Codex #9). Read our own nonce from this
// module's URL and prefer the registry; fall back to the legacy single object
// (older hosts) and finally to a local #root for `npm run dev`.
const __m = new URL(import.meta.url).searchParams.get("m");
const boot =
  (__m && window.__BIFROST_APPS__ && window.__BIFROST_APPS__[__m]) ||
  window.__BIFROST_APP__;
const mountEl = boot?.mountEl ?? document.getElementById("root")!;
const basename = boot?.basename ?? "/";
const baseUrl = boot?.baseUrl ?? import.meta.env.VITE_BIFROST_API_URL ?? window.location.origin;
const token = boot?.token ?? import.meta.env.VITE_BIFROST_TOKEN ?? "";
// Precedence (boot over VITE env) is locked by client/src/lib/app-sdk/dev-bootstrap.test.ts
const orgScope = boot?.orgScope ?? import.meta.env.VITE_BIFROST_ORG_ID ?? null;
// This app's id, so useWorkflow scopes path refs to THIS install's workflow.
const appId = boot?.appId ?? import.meta.env.VITE_BIFROST_APP_ID ?? null;
// Platform theme, so a theme-aware app starts in sync. Add supportsTheme to
// BifrostProvider (and key your styles off the `dark` class) to opt into the
// header's light/dark toggle.
const theme = boot?.theme ?? "light";

const root = createRoot(mountEl);
// Let the platform tear this root down on navigation (no leak).
boot?.registerUnmount?.(() => root.unmount());

root.render(
  <StrictMode>
    <BifrostProvider baseUrl={baseUrl} token={token} orgScope={orgScope} appId={appId} theme={theme} onLogout={boot?.onLogout}>
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </BifrostProvider>
  </StrictMode>,
);
"""
    app_tsx = """\
import { Routes, Route, Link } from "react-router-dom";
import { BifrostHeader, useWorkflowMutation } from "bifrost";

function Home() {
  // Workflow hooks (pick by intent — same mental model as React Query):
  //   useWorkflowQuery(ref)    → READ: auto-runs on mount, has { data, refresh }.
  //   useWorkflowMutation(ref) → ACTION: runs on mutate(), has { mutate }.
  // This sample is a button (an action), so it uses the mutation hook. The ref
  // is a workflow UUID or a portable `path::function` ref (e.g.
  // "functions/hello.py::main", shipped with this scaffold). Bare names are NOT
  // resolvable — names aren't unique, so the execute endpoint 404s on them.
  const wf = useWorkflowMutation<{ message: string }>("functions/hello.py::main");
  return (
    <main style={{ padding: 24 }}>
      <h1>Hello from your Bifrost app</h1>
      <p>
        <Link to="/about">About</Link>
      </p>
      <button onClick={() => wf.mutate({})} disabled={wf.loading}>
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


# Dirs whose .py is never solution workflow source: generated/dep/manifest output
# (mirrors the local function host's skip set) — kept layout-agnostic so a
# developer can organize freely (functions/, lib/, …), matching how
# `solution start` discovers and how the platform resolves path::fn (root-relative,
# folder-indifferent). App source dirs are excluded separately (apps are bundled
# by _collect_apps; their .py must not double-collect as workflow source).
_PY_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


def _app_source_dirs(workspace: pathlib.Path) -> set[str]:
    """Relative (POSIX) app source dirs from .bifrost/apps.yaml, to exclude from
    the Python-source sweep (apps are bundled by _collect_apps)."""
    manifest = workspace / ".bifrost" / "apps.yaml"
    if not manifest.is_file():
        return set()
    data = yaml.safe_load(manifest.read_text()) or {}
    out: set[str] = set()
    for body in (data.get("apps", {}) or {}).values():
        if isinstance(body, dict) and body.get("path"):
            out.add(str(body["path"]).strip("/"))
    return out


def _collect_python_files(workspace: pathlib.Path) -> dict[str, str]:
    """Collect installable Python source (relative path → text), layout-agnostic.

    Scans the whole solution root for ``.py``, excluding generated/dep/manifest
    dirs and the separately-bundled app source dirs. A workflow under ANY folder
    (``functions/``, ``lib/``, …) is collected — the deploy roots must agree with
    where the scaffold writes / where ``solution start`` resolves, else a workflow
    deploys with a row but no code (shakeout HIGH).
    """
    app_dirs = _app_source_dirs(workspace)
    files: dict[str, str] = {}
    for py in workspace.rglob("*.py"):
        rel_parts = py.relative_to(workspace).parts
        if any(part in _PY_SKIP_DIRS for part in rel_parts):
            continue
        rel = py.relative_to(workspace).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in app_dirs):
            continue
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


def _collect_config_schemas(workspace: pathlib.Path) -> list[dict]:
    """Read config DECLARATIONS from .bifrost/configs.yaml (keyed by key/UUID).

    Declarations ONLY — there is no ``value`` field by design. Config values are
    instance-owned and supplied at install time; local dev reads them from .env.
    """
    cfg_file = workspace / ".bifrost" / "configs.yaml"
    if not cfg_file.is_file():
        return []
    data = yaml.safe_load(cfg_file.read_text()) or {}
    raw = data.get("configs", {})
    entries: list[dict] = []
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        entries.append({
            "id": body.get("id", key),
            "key": body.get("key") or key,
            "type": body.get("type", "string"),
            "required": bool(body.get("required", False)),
            "description": body.get("description"),
            "default": body.get("default"),
            "position": int(body.get("position", 0)),
        })
    return entries


def _collect_claims(workspace: pathlib.Path) -> list[dict]:
    """Read Custom Claim definitions from .bifrost/claims.yaml (keyed by UUID)."""
    claims_file = workspace / ".bifrost" / "claims.yaml"
    if not claims_file.is_file():
        return []
    data = yaml.safe_load(claims_file.read_text()) or {}
    raw = data.get("claims", {})
    entries: list[dict] = []
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        entries.append({
            "id": body.get("id", key),
            "name": body.get("name") or key,
            "description": body.get("description"),
            "type": body.get("type", "list"),
            "query": body["query"],
        })
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
# Content types for an app `logo:` file (manifest → deploy → Application row).
# Mirrors the server's LOGO_ALLOWED_CONTENT_TYPES; the deployer re-validates.
_LOGO_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
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

        # App LOGO: the manifest may point `logo:` at an image file relative to
        # the app dir (e.g. "public/logo.svg"). Read + carry it base64 so the
        # deploy can stamp it on the Application row — the only way a Solution
        # app can ship a logo (the upload endpoint is blocked for solution-
        # managed apps). The deployer sanitizes/limits; we just read.
        logo_b64: str | None = None
        logo_content_type: str | None = None
        logo_path = body.get("logo")
        if logo_path:
            logo_file = app_dir / logo_path
            if logo_file.is_file():
                logo_b64 = base64.b64encode(logo_file.read_bytes()).decode("ascii")
                logo_content_type = _LOGO_CONTENT_TYPES.get(logo_file.suffix.lower())
            else:
                raise click.ClickException(
                    f"app '{key}': logo file not found at {logo_file}"
                )

        entries.append({
            "id": body.get("id", key),
            "slug": body.get("slug") or key,
            "name": body.get("name") or key,
            # description is deploy-owned: _upsert_apps full-replaces it, so
            # dropping it here would CLEAR the deployed app's description on every
            # deploy (non-round-tripping — Codex #16).
            "description": body.get("description"),
            "app_model": body.get("app_model", "inline_v1"),
            "dependencies": body.get("dependencies") or {},
            "access_level": body.get("access_level"),
            # Role bindings the deployer syncs into AppRole (Codex P1-d). Carry
            # both raw UUIDs and portable names; the deployer prefers names.
            "roles": body.get("roles") or [],
            "role_names": body.get("role_names"),
            "logo_b64": logo_b64,
            "logo_content_type": logo_content_type,
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
@click.option("--force", is_flag=True, default=False,
              help="Apply even if the bundle version is older than the installed version (downgrade).")
def deploy_cmd(path: str, solution_id: str | None, force: bool) -> None:
    workspace = pathlib.Path(path).resolve()
    if not is_solution_workspace(workspace):
        raise click.ClickException(
            f"No {DESCRIPTOR_FILENAME} in {workspace} — not a Solution workspace. "
            f"Run `bifrost solution init` first."
        )
    descriptor = load_descriptor(workspace)

    # Solution-level icon: `logo:` in bifrost.solution.yaml points at an image
    # relative to the workspace root; carried base64 for deploy to stamp on the
    # install (shown on the /solutions catalog). Errors loudly when missing.
    solution_logo_b64: str | None = None
    solution_logo_content_type: str | None = None
    if descriptor.logo:
        import base64

        logo_file = workspace / descriptor.logo
        if not logo_file.is_file():
            raise click.ClickException(f"solution logo file not found at {logo_file}")
        solution_logo_b64 = base64.b64encode(logo_file.read_bytes()).decode("ascii")
        solution_logo_content_type = _LOGO_CONTENT_TYPES.get(logo_file.suffix.lower())

    python_files = _collect_python_files(workspace)
    workflows = _collect_workflows(workspace)
    tables = _collect_tables(workspace)
    apps = _collect_apps(workspace)
    forms = _collect_forms(workspace)
    agents = _collect_agents(workspace)
    claims = _collect_claims(workspace)
    config_schemas = _collect_config_schemas(workspace)

    async def _run() -> int:
        client = BifrostClient.get_instance(require_auth=True)

        target_id = solution_id
        if target_id is None:
            # Resolve or create the install by (slug, scope).
            resp = await client.get("/api/solutions")
            if resp.status_code != 200:
                raise click.ClickException(
                    f"Failed to list installs ({resp.status_code}): {resp.text[:200]}"
                )
            installs = resp.json().get("solutions", [])
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
            "claims": claims,
            "config_schemas": config_schemas,
            "version": descriptor.version,
            "logo_b64": solution_logo_b64,
            "logo_content_type": solution_logo_content_type,
            "force": force,
        })
        if deploy.status_code == 409 and "older than installed" in deploy.text:
            # Task 20 downgrade gate — surface the server's detail and how to
            # override it deliberately.
            detail = deploy.json().get("detail", deploy.text)
            raise click.ClickException(f"{detail}\nRe-run with --force to downgrade.")
        if deploy.status_code not in (200, 201):
            click.echo(f"Deploy failed: {deploy.status_code} {deploy.text}", err=True)
            return 1
        body = deploy.json()
        click.echo(
            f"Deployed install {target_id}: "
            f"{body.get('workflows_upserted', 0)} workflow(s) upserted, "
            f"{body.get('claims_upserted', 0)} claim(s) upserted, "
            f"{body.get('workflows_deleted', 0)} deleted."
        )
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)


@solution_group.command(
    name="install",
    help="Install a Solution from a workspace zip (drag-and-drop equivalent).",
)
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--org", "org_id", default=None, help="Target org id (omit for a global install).")
@click.option(
    "--set",
    "set_values",
    multiple=True,
    help="Config value KEY=VALUE (repeatable). Applied atomically with the deploy.",
)
def install_cmd(zip_path: str, org_id: str | None, set_values: tuple[str, ...]) -> None:
    """POST a Solution workspace zip to ``/api/solutions/install``.

    The server unzips it, resolves-or-creates the install, deploys the bundle,
    and applies any ``--set`` config values atomically under the install lock.
    """
    config_values: dict[str, str] = {}
    for pair in set_values:
        if "=" not in pair:
            raise click.ClickException(f"--set expects KEY=VALUE, got: {pair}")
        key, _, value = pair.partition("=")
        config_values[key] = value

    zip_bytes = pathlib.Path(zip_path).read_bytes()

    async def _run() -> int:
        client = BifrostClient.get_instance(require_auth=True)
        form: dict[str, str] = {"config_values": json.dumps(config_values)}
        if org_id:
            form["organization_id"] = org_id
        resp = await client.post(
            "/api/solutions/install",
            files={"file": (pathlib.Path(zip_path).name, zip_bytes, "application/zip")},
            data=form,
        )
        if resp.status_code not in (200, 201):
            click.echo(f"Install failed: {resp.status_code} {resp.text}", err=True)
            return 1
        body = resp.json()
        click.echo(f"Installed solution {body['id']} (slug={body.get('slug')}).")
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)


@solution_group.command(name="start", help="Run the app's dev server + local workflows (one origin).")
@click.argument("app_slug", required=False)
@click.option("--org", "org_ref", default=None, help="Org ref (UUID or name) to run under (superuser).")
@click.option("--port", default=3000, show_default=True, type=int, help="Local origin port.")
def start_cmd(app_slug: str | None, org_ref: str | None, port: int) -> None:
    import shutil

    from bifrost.client import BifrostClient
    from bifrost.solution_dev.app_select import AppSelectionError, select_app
    from bifrost.solution_dev.function_host import FunctionHost, set_dev_execution_context
    from bifrost.solution_dev.scaffold_check import PATCH_HINT, main_tsx_needs_dev_fallback

    workspace = pathlib.Path(".").resolve()
    if not is_solution_workspace(workspace):
        raise click.ClickException(
            f"Not a Solution workspace (no {DESCRIPTOR_FILENAME}). Run `bifrost solution init` first."
        )

    client = BifrostClient.get_instance(require_auth=True)

    org_info = client.organization
    if org_ref:
        from bifrost.refs import RefResolver
        resolver = RefResolver(client)
        org_id = asyncio.run(resolver.resolve("org", org_ref))
        resp = client._sync_http.get("/api/sdk/context", params={"org_id": org_id})
        if resp.status_code == 403:
            raise click.ClickException("--org requires superuser privileges.")
        if resp.status_code >= 400:
            raise click.ClickException(f"Could not resolve org '{org_ref}': HTTP {resp.status_code}")
        org_info = resp.json().get("organization", org_info)

    try:
        chosen = select_app(workspace, slug=app_slug)
    except AppSelectionError as exc:
        raise click.ClickException(str(exc))

    main_tsx = chosen.app_dir / "src" / "main.tsx"
    if main_tsx_needs_dev_fallback(main_tsx):
        click.echo(PATCH_HINT, err=True)

    set_dev_execution_context(user=client.user, org=org_info)

    host = FunctionHost(workspace)
    host.reload()
    click.echo(f"Discovered {len(host.refs())} local function(s).")

    # Spawn npm via the RESOLVED path: shutil.which honors PATHEXT (finds
    # `npm.cmd` on Windows) but CreateProcess with a literal "npm" argv[0] does
    # not — a bare "npm" spawn raises FileNotFoundError there.
    npm = shutil.which("npm")
    if npm is None:
        raise click.ClickException("npm not found on PATH — install Node.js to run the dev server.")
    if not (chosen.app_dir / "node_modules").is_dir():
        click.echo("Installing app dependencies (npm install)…")
        subprocess.run([npm, "install"], cwd=chosen.app_dir, check=True)

    vite_env = dict(os.environ)
    vite_env["VITE_BIFROST_APP_ID"] = chosen.app_id
    vite_env["VITE_BIFROST_ORG_ID"] = (org_info or {}).get("id", "")
    vite_env["BIFROST_API_URL"] = client.api_url
    vite_env["BIFROST_ACCESS_TOKEN"] = client._access_token

    vite_port = port + 1
    # Run `npm run dev` in its OWN process group (start_new_session) so teardown
    # can signal the WHOLE group: `npm` spawns the real `vite` node process as a
    # child, and a plain terminate() of `npm` orphans `vite` (it keeps the port
    # bound). Killing the group reaps both. (POSIX; Windows falls back to a plain
    # terminate of the npm process.)
    vite_proc = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(vite_port), "--strictPort"],
        cwd=chosen.app_dir, env=vite_env,
        start_new_session=True,
    )

    try:
        asyncio.run(_serve(client, chosen, org_info, host, port, vite_port, workspace))
    finally:
        _terminate_process_group(vite_proc)


# ── capture: adopt loose _repo/ entities into an install (migration) ─────────

# Selector kinds whose values resolve by NAME against the candidates listing.
# (Configs are keyed by their string key directly, so they need no resolution.)
_CAPTURE_NAME_KINDS = ("workflows", "tables", "apps", "forms", "agents", "claims")


def _resolve_capture_selectors(
    candidates: dict, raw: dict[str, tuple[str, ...]]
) -> dict[str, list[str]]:
    """Map user-supplied selector values (NAME or id) to entity ids.

    ``candidates`` is the ``/capture/candidates`` payload — the loose same-scope
    universe the install can adopt. A value that already looks like one of the
    listed ids passes through; otherwise it's matched by ``name``. Unknown values
    raise so the migration fails loudly instead of silently capturing nothing.
    Configs pass through verbatim (they're keyed by string key, not id).
    """
    resolved: dict[str, list[str]] = {}
    for kind in _CAPTURE_NAME_KINDS:
        rows = candidates.get(kind) or []
        by_id = {str(r["id"]): str(r["id"]) for r in rows}
        by_name = {r["name"]: str(r["id"]) for r in rows}
        out: list[str] = []
        for value in raw.get(kind, ()):  # type: ignore[arg-type]
            if value in by_id:
                out.append(by_id[value])
            elif value in by_name:
                out.append(by_name[value])
            else:
                raise click.ClickException(
                    f"no loose {kind[:-1]} named or id'd '{value}' is capturable "
                    f"by this install (not in /capture/candidates for its scope)."
                )
        resolved[kind] = out
    resolved["configs"] = list(raw.get("configs", ()))
    return resolved


def _print_capture_preview(preview: dict) -> None:
    """Render the dependency walker's preview for ``--dry-run``."""
    pulled = preview.get("pulled_in") or []
    outside = preview.get("outside_references") or []
    if pulled:
        click.echo("Will also pull in (forward dependency closure):")
        for d in pulled:
            click.echo(f"  + {d['kind']}: {d['name']}")
    else:
        click.echo("Nothing extra is pulled in beyond your selection.")
    if outside:
        click.echo("")
        click.echo("⚠ Outside references (left loose, will point across the boundary):")
        for r in outside:
            click.echo(
                f"  {r['referencer_kind']} '{r['referencer_name']}' still uses "
                f"{r['target_kind']} '{r['target_name']}'"
            )
    click.echo("")
    click.echo(
        "Note: the dependency scan is static — computed/dynamic refs "
        "(importlib, variable table names) are invisible. Review before applying."
    )


@solution_group.command(
    name="capture",
    help="Adopt loose _repo/ entities into an install (migration). "
    "--dry-run previews the dependency closure + outside references first.",
)
@click.argument("solution_id")
@click.option("--workflow", "workflows", multiple=True, help="Workflow name or id (repeatable).")
@click.option("--table", "tables", multiple=True, help="Table name or id (repeatable).")
@click.option("--app", "apps", multiple=True, help="App name or id (repeatable).")
@click.option("--form", "forms", multiple=True, help="Form name or id (repeatable).")
@click.option("--agent", "agents", multiple=True, help="Agent name or id (repeatable).")
@click.option("--claim", "claims", multiple=True, help="Custom-claim name or id (repeatable).")
@click.option("--config", "configs", multiple=True, help="Config key (repeatable).")
@click.option(
    "--include-imports/--no-include-imports", default=False, show_default=True,
    help="Also bundle the transitive modules/ import closure of captured workflows.",
)
@click.option("--dry-run", is_flag=True, default=False,
              help="Preview the dependency closure + outside references; capture nothing.")
def capture_cmd(
    solution_id: str,
    workflows: tuple[str, ...],
    tables: tuple[str, ...],
    apps: tuple[str, ...],
    forms: tuple[str, ...],
    agents: tuple[str, ...],
    claims: tuple[str, ...],
    configs: tuple[str, ...],
    include_imports: bool,
    dry_run: bool,
) -> None:
    raw = {
        "workflows": workflows, "tables": tables, "apps": apps, "forms": forms,
        "agents": agents, "claims": claims, "configs": configs,
    }
    if not any(raw.values()):
        raise click.ClickException(
            "no entities selected — pass at least one of "
            "--workflow/--table/--app/--form/--agent/--claim/--config."
        )

    async def _run() -> int:
        client = BifrostClient.get_instance(require_auth=True)
        cand = await client.get(f"/api/solutions/{solution_id}/capture/candidates")
        if cand.status_code != 200:
            raise click.ClickException(
                f"Failed to list capture candidates ({cand.status_code}): "
                f"{cand.text[:200]}"
            )
        selectors = _resolve_capture_selectors(cand.json(), raw)
        body = {**selectors, "include_imports": include_imports}

        if dry_run:
            preview = await client.post(
                f"/api/solutions/{solution_id}/capture/preview", json=body
            )
            if preview.status_code != 200:
                raise click.ClickException(
                    f"Preview failed ({preview.status_code}): {preview.text[:200]}"
                )
            _print_capture_preview(preview.json())
            click.echo("Dry run — nothing was captured.")
            return 0

        resp = await client.post(
            f"/api/solutions/{solution_id}/capture", json=body
        )
        if resp.status_code not in (200, 201):
            raise click.ClickException(
                f"Capture failed ({resp.status_code}): {resp.text[:300]}"
            )
        r = resp.json()
        click.echo(
            f"Captured into install {solution_id}: "
            f"{r.get('workflows_captured', 0)} workflow(s), "
            f"{r.get('tables_captured', 0)} table(s), "
            f"{r.get('apps_captured', 0)} app(s), "
            f"{r.get('forms_captured', 0)} form(s), "
            f"{r.get('agents_captured', 0)} agent(s), "
            f"{r.get('claims_captured', 0)} claim(s), "
            f"{r.get('config_declarations_captured', 0)} config declaration(s)."
        )
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)


@solution_group.command(
    name="swap-slugs",
    help="Atomically exchange two apps' slugs (v1→v2 migration cutover).",
)
@click.argument("app_a")
@click.argument("app_b")
def swap_slugs_cmd(app_a: str, app_b: str) -> None:
    """Give the v2 app the live slug and park the v1 app under the other slug.

    Accepts app ids or slugs for both arguments. The swap is one transaction
    holding the slug advisory lock, so ``/apps/{slug}`` bookmarks survive the
    cutover with no unowned-slug window. Solution-managed apps are refused (slug
    is a deploy-owned property for those).
    """

    async def _run() -> int:
        client = BifrostClient.get_instance(require_auth=True)

        async def _resolve(ref: str) -> str:
            # An id passes straight through; a slug resolves via GET /{slug}.
            try:
                import uuid as _uuid

                _uuid.UUID(ref)
                return ref
            except (ValueError, AttributeError):
                pass
            resp = await client.get(f"/api/applications/{ref}")
            if resp.status_code != 200:
                raise click.ClickException(
                    f"No application '{ref}' ({resp.status_code}): {resp.text[:160]}"
                )
            return resp.json()["id"]

        a_id = await _resolve(app_a)
        b_id = await _resolve(app_b)
        resp = await client.post(
            "/api/applications/swap-slugs", json={"app_a": a_id, "app_b": b_id}
        )
        if resp.status_code not in (200, 201):
            raise click.ClickException(
                f"Slug swap failed ({resp.status_code}): {resp.text[:300]}"
            )
        apps = resp.json().get("applications", [])
        for app in apps:
            click.echo(f"  {app['name']} → /apps/{app['slug']}")
        click.echo("Slug swap complete.")
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)


def _terminate_process_group(proc: "subprocess.Popen") -> None:
    """Stop a child and any grandchildren it spawned in its process group.

    `npm run dev` forks `vite`; killing only `npm` leaves `vite` holding the
    port. SIGTERM the group, wait briefly, then SIGKILL the group if needed.
    """
    import signal

    def _signal_group(sig: int) -> None:
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
                return
            except (ProcessLookupError, PermissionError):
                return  # already gone / not our group — fall through to proc-level
        # No process groups (Windows): signal the process itself.
        proc.send_signal(sig)

    _signal_group(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_group(signal.SIGKILL)


async def _serve(client, chosen, org_info, host, port, vite_port, workspace):
    from aiohttp import web

    from bifrost.solution_dev.proxy import DevProxyConfig, build_dev_app
    from bifrost.solution_dev.reload import start_function_watch

    cfg = DevProxyConfig(
        upstream_url=client.api_url.rstrip("/"),
        token=client._access_token,
        app_id=chosen.app_id,
        org_id=(org_info or {}).get("id"),
    )
    app = build_dev_app(cfg, host, vite_url=f"http://127.0.0.1:{vite_port}")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    observer = start_function_watch(workspace, host)
    click.echo(f"\n  Bifrost solution dev server → http://localhost:{port}\n")
    click.echo("  Press Ctrl-C to stop.\n")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        observer.stop()
        observer.join(timeout=2)
        await runner.cleanup()


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
    # ClickException covers UsageError's siblings too (e.g. the ClickException
    # that start_cmd/deploy_cmd/install_cmd raise on a handled error). Without
    # this, standalone_mode=False lets it escape as an uncaught traceback instead
    # of the intended one-line "Error: ..." message.
    except click.ClickException as exc:
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
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


__all__ = ["solution_group", "handle_solution", "handle_deploy"]
