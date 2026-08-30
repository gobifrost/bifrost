"""Local lifecycle for independently managed V2 Apps."""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import zipfile

import click

from bifrost.app_binding import AppBinding, find_app_root, read_app_binding, write_app_binding
from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

APP_DEPLOY_TIMEOUT_SECONDS = 20 * 60


@click.group(name="app")
def app_group() -> None:
    """Create, bind, run, and deploy an App project."""


def _client(api_url: str | None = None) -> BifrostClient:
    return BifrostClient.get_instance(require_auth=True, api_url=api_url)


def _slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        raise click.ClickException("App slug must begin with a letter.")
    return slug


def _scaffold(root: pathlib.Path, slug: str) -> None:
    if root.exists() and any(root.iterdir()):
        raise click.ClickException(f"{root} is not empty.")
    root.mkdir(parents=True, exist_ok=True)
    # The V2 runtime skeleton is shared with Solution Apps; only its local
    # workflow/deploy language differs. This keeps both runtimes on one SDK
    # mount contract while App projects remain ordinary Vite repositories.
    from bifrost.commands.solution import _v2_scaffold_files

    replacements = {
        "bifrost solution start": "bifrost app start",
        "bifrost deploy": "bifrost app deploy",
        "standalone_v2 app": "V2 App",
        "this Solution's own workflow": "the live platform workflow",
        "THIS install's own workflow": "the live platform workflow",
        "both from your local files": "against your live Bifrost environment",
        '"functions/hello.py::main",\n  // shipped with this scaffold) or a workflow name — both resolve to THIS\n  // install\'s own workflow when deployed, and `bifrost app start` runs\n  // against your live Bifrost environment. (Avoid raw UUID refs: deploy remaps entity\n  // ids per install, so a hardcoded UUID won\'t resolve on a deployed install.)':
            '"workflows/hello.py::main") or a workflow name. Replace this sample\n  // with a workflow that exists in the live Bifrost environment selected by\n  // `bifrost app start`. App identity and runtime organization scope are passed\n  // separately, so the same source can be debugged against an authorized org.',
        '"functions/hello.py::main")': '"workflows/hello.py::main")',
    }
    for rel, content in _v2_scaffold_files(slug).items():
        for old, new in replacements.items():
            content = content.replace(old, new)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    gitignore = root / ".gitignore"
    gitignore.write_text(".env\nnode_modules/\ndist/\n", encoding="utf-8")


async def _resolve_org(client: BifrostClient, ref: str) -> str:
    return await RefResolver(client).resolve("org", ref)


async def _find_app(client: BifrostClient, ref: str) -> dict:
    app_id = await RefResolver(client).resolve("app", ref)
    response = await client.get("/api/applications")
    response.raise_for_status()
    apps = response.json().get("applications", [])
    match = next((app for app in apps if str(app.get("id")) == app_id), None)
    if match is None:
        raise click.ClickException(f"App {app_id} is not visible in the current scope.")
    if match.get("app_model") != "standalone_v2" or match.get("is_solution_managed"):
        raise click.ClickException("The selected App is not independently managed.")
    return match


def _create_project(
    root: pathlib.Path,
    *,
    name: str | None,
    slug: str | None,
    org_ref: str | None,
    is_global: bool,
    api_url: str | None,
) -> tuple[dict, str]:
    if org_ref and is_global:
        raise click.UsageError("Use either --org or --global, not both.")
    if root.exists() and any(root.iterdir()):
        raise click.ClickException(f"{root} is not empty.")
    display_name = name or (root.name if root.name not in {"", "."} else "my-app")
    app_slug = slug or _slugify(display_name)

    async def run() -> tuple[dict, str]:
        client = _client(api_url)
        body: dict[str, object] = {
            "name": display_name,
            "slug": app_slug,
            "app_model": "standalone_v2",
        }
        if org_ref:
            body["organization_id"] = await _resolve_org(client, org_ref)
        elif is_global:
            body["organization_id"] = None
        response = await client.post("/api/applications", json=body)
        response.raise_for_status()
        return response.json(), client.api_url

    app, selected_api_url = asyncio.run(run())
    try:
        _scaffold(root, app_slug)
        write_app_binding(
            root,
            AppBinding(api_url=selected_api_url, app_id=str(app["id"])),
        )
    except Exception:
        # The remote row is intentionally retained: deleting it automatically
        # would be a destructive side effect after a local filesystem failure.
        raise
    return app, app_slug


@app_group.command("create")
@click.argument("path", default=".", type=click.Path(file_okay=False))
@click.option("--name", default=None, help="App display name (default: directory name).")
@click.option("--slug", default=None, help="URL slug (default: derived from name).")
@click.option("--org", "org_ref", default=None, help="Organization UUID or name.")
@click.option("--global", "is_global", is_flag=True, help="Create a globally visible App.")
@click.option("--url", "api_url", default=None, help="Bifrost instance URL.")
def create_cmd(
    path: str,
    name: str | None,
    slug: str | None,
    org_ref: str | None,
    is_global: bool,
    api_url: str | None,
) -> None:
    """Create a Vite project and its remote App record."""
    root = pathlib.Path(path).resolve()
    app, _ = _create_project(
        root,
        name=name,
        slug=slug,
        org_ref=org_ref,
        is_global=is_global,
        api_url=api_url,
    )
    click.echo(f"Created App {app['id']} in {root}")
    click.echo("Run `npm install`, then `bifrost app start`.")


@app_group.command("migrate")
@click.argument("source", type=click.Path(exists=True, file_okay=False))
@click.argument("path", type=click.Path(file_okay=False))
@click.option("--name", default=None, help="App display name (default: destination name).")
@click.option("--slug", default=None, help="Temporary V2 URL slug (default: derived from name).")
@click.option("--org", "org_ref", default=None, help="Organization UUID or name.")
@click.option("--global", "is_global", is_flag=True, help="Create a globally visible App.")
@click.option("--url", "api_url", default=None, help="Bifrost instance URL.")
def migrate_cmd(
    source: str,
    path: str,
    name: str | None,
    slug: str | None,
    org_ref: str | None,
    is_global: bool,
    api_url: str | None,
) -> None:
    """Migrate a pulled v1 App directory into an independent V2 App project."""
    source_root = pathlib.Path(source).resolve()
    root = pathlib.Path(path).resolve()
    if source_root == root or source_root in root.parents:
        raise click.ClickException("Migration destination must be outside the v1 source directory.")
    app, app_slug = _create_project(
        root,
        name=name,
        slug=slug,
        org_ref=org_ref,
        is_global=is_global,
        api_url=api_url,
    )
    from bifrost.app_migration import migrate_v1_source

    migrate_v1_source(
        source_root,
        root,
        title=name or app_slug,
        lifecycle="app",
    )
    click.echo(f"Created migrated App {app['id']} in {root}")


@app_group.command("bind")
@click.argument("ref")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--url", "api_url", default=None, help="Bifrost instance URL.")
def bind_cmd(ref: str, path: str, api_url: str | None) -> None:
    """Bind a cloned Vite project to an existing App."""
    root = pathlib.Path(path).resolve()
    if not (root / "package.json").is_file():
        raise click.ClickException(f"{root} is not a Vite project (package.json missing).")

    async def run() -> tuple[dict, BifrostClient]:
        client = _client(api_url)
        return await _find_app(client, ref), client

    app, client = asyncio.run(run())
    write_app_binding(root, AppBinding(client.api_url, str(app["id"])))
    click.echo(f"Bound App {app['id']} in {root / '.env'}")


def _bound_project(path: str) -> tuple[pathlib.Path, AppBinding]:
    requested = pathlib.Path(path).resolve()
    root = find_app_root(requested) if path == "." else requested
    binding = read_app_binding(root) if root else None
    if root is None or binding is None:
        raise click.ClickException(
            "No App binding found. Run `bifrost app create` or `bifrost app bind`."
        )
    return root, binding


def _zip_project(root: pathlib.Path, destination: pathlib.Path) -> None:
    from bifrost.cli import _build_file_filter

    matcher = _build_file_filter(root)
    included = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(root.rglob("*")):
            if not file.is_file():
                continue
            rel = file.relative_to(root).as_posix()
            if matcher.match_file(rel):
                continue
            archive.write(file, rel)
            included += 1
    if included == 0:
        raise click.ClickException("The App project contains no deployable files.")


async def swap_app_slugs(client: BifrostClient, app_a: str, app_b: str) -> None:
    """Resolve two App refs and atomically exchange their public slugs."""
    from uuid import UUID

    async def resolve(ref: str) -> str:
        try:
            UUID(ref)
            return ref
        except (TypeError, ValueError):
            response = await client.get(f"/api/applications/{ref}")
            if response.status_code != 200:
                raise click.ClickException(
                    f"No application '{ref}' ({response.status_code}): "
                    f"{response.text[:160]}"
                )
            return str(response.json()["id"])

    response = await client.post(
        "/api/applications/swap-slugs",
        json={"app_a": await resolve(app_a), "app_b": await resolve(app_b)},
    )
    if response.status_code not in (200, 201):
        raise click.ClickException(
            f"Slug swap failed ({response.status_code}): {response.text[:300]}"
        )
    for app in response.json().get("applications", []):
        click.echo(f"  {app['name']} → /apps/{app['slug']}")
    click.echo("Slug swap complete.")


@app_group.command("swap-slugs")
@click.argument("app_a")
@click.argument("app_b")
@click.option("--url", "api_url", default=None, help="Bifrost instance URL.")
def swap_slugs_cmd(app_a: str, app_b: str, api_url: str | None) -> None:
    """Atomically exchange v1 and independent V2 App slugs during cutover."""
    asyncio.run(swap_app_slugs(_client(api_url), app_a, app_b))


async def _wait_for_deploy(client: BifrostClient, job_id: str) -> dict:
    deadline = time.monotonic() + APP_DEPLOY_TIMEOUT_SECONDS
    last_phase = None
    while time.monotonic() < deadline:
        response = await client.get(f"/api/platform-jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        phase = (job.get("progress") or {}).get("phase")
        if phase and phase != last_phase:
            click.echo(phase, err=True)
            last_phase = phase
        if job.get("status") == "succeeded":
            return job
        if job.get("status") in {"failed", "cancelled"}:
            error = (job.get("error") or {}).get("message") or job.get("status")
            raise click.ClickException(f"App deploy failed: {error}")
        await asyncio.sleep(2)
    raise click.ClickException(f"App deploy timed out; job {job_id} is still running.")


@app_group.command("deploy")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
def deploy_cmd(path: str) -> None:
    """Build and atomically deploy the current App project."""
    root, binding = _bound_project(path)

    async def run(zip_path: pathlib.Path) -> dict:
        client = _client(binding.api_url)
        with zip_path.open("rb") as source:
            response = await client.post(
                f"/api/applications/{binding.app_id}/deploy",
                files={"source": ("app-source.zip", source, "application/zip")},
            )
        response.raise_for_status()
        accepted = response.json()
        click.echo(f"Deploy job {accepted['job_id']}", err=True)
        return await _wait_for_deploy(client, str(accepted["job_id"]))

    with tempfile.TemporaryDirectory(prefix="bifrost-app-cli-") as tmp:
        zip_path = pathlib.Path(tmp) / "source.zip"
        _zip_project(root, zip_path)
        job = asyncio.run(run(zip_path))
    click.echo(f"App deployed: {job.get('result', {}).get('application_id', binding.app_id)}")


@app_group.command("start")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--org", "org_ref", default=None, help="Run against another authorized organization.")
@click.option("--port", default=3000, show_default=True, type=int)
@click.option("--host", "bind_host", default="127.0.0.1", show_default=True)
@click.option("--public-url", default=None)
def start_cmd(
    path: str,
    org_ref: str | None,
    port: int,
    bind_host: str,
    public_url: str | None,
) -> None:
    """Run the local Vite App against live Bifrost resources."""
    from bifrost.commands.solution import (
        _ensure_port_free,
        _terminate_process_group,
        _vite_child_env,
        _wait_for_vite,
    )
    from bifrost.solution_dev.proxy import DevProxyConfig, build_dev_app
    from aiohttp import web

    root, binding = _bound_project(path)

    async def scope() -> tuple[BifrostClient, str | None]:
        client = _client(binding.api_url)
        if org_ref:
            return client, await _resolve_org(client, org_ref)
        response = await client.get("/api/sdk/context")
        response.raise_for_status()
        organization = response.json().get("organization") or {}
        return client, str(organization.get("id")) if organization.get("id") else None

    client, org_id = asyncio.run(scope())
    npm = shutil.which("npm")
    if npm is None:
        raise click.ClickException("npm not found on PATH — install Node.js first.")
    vite_port = port + 1
    _ensure_port_free(port)
    _ensure_port_free(vite_port)

    if not (root / "node_modules").is_dir() or not (root / "node_modules/bifrost").is_dir():
        sdk = f"bifrost@{client.api_url.rstrip('/')}/api/sdk/download"
        click.echo("Installing App dependencies and this instance's SDK…")
        subprocess.run(
            [npm, "install", "--no-save", "--package-lock=false", sdk],
            cwd=root,
            check=True,
        )

    env = _vite_child_env(
        dict(os.environ),
        app_id=binding.app_id,
        org_id=org_id,
        access_token=client._access_token,
    )
    vite = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(vite_port), "--strictPort"],
        cwd=root,
        env=env,
        start_new_session=True,
    )
    try:
        _wait_for_vite(vite, vite_port)
        cfg = DevProxyConfig(
            upstream_url=client.api_url,
            token=client._access_token,
            app_id=binding.app_id,
            org_id=org_id,
            solution_id=None,
            global_repo_access=True,
            local_workflows=False,
            refresh_token=client.refresh_access_token,
        )

        async def serve() -> None:
            app = build_dev_app(cfg, None, f"http://127.0.0.1:{vite_port}")
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, bind_host, port).start()
            origin = (public_url or f"http://127.0.0.1:{port}").rstrip("/")
            click.echo(f"\n  Bifrost App → {origin}")
            click.echo(f"  Live organization scope: {org_id or 'current user'}")
            click.echo("  Press Ctrl-C to stop.\n")
            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                await runner.cleanup()

        asyncio.run(serve())
    finally:
        _terminate_process_group(vite)


def handle_app(args: list[str]) -> int:
    try:
        app_group.main(args=args, standalone_mode=False, prog_name="bifrost app")
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code


__all__ = ["app_group", "handle_app"]
