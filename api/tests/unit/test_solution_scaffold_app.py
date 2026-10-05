"""`bifrost solution scaffold-app` writes a working standalone_v2 skeleton with
the CLI-login dev loop wired in — no token pasting (Codex R4 DX)."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import yaml  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from bifrost.commands.solution import _v2_scaffold_files, solution_group  # noqa: E402


def _init_workspace(root: pathlib.Path) -> None:
    (root / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")


def test_scaffold_files_shape_and_dev_wiring() -> None:
    files = _v2_scaffold_files("my-app")

    # All the files a normal Vite app needs.
    for f in ("package.json", "vite.config.ts", "index.html", "src/main.tsx",
              "src/App.tsx", ".env.example", "README.md"):
        assert f in files, f"{f} not scaffolded"

    pkg = json.loads(files["package.json"])
    assert pkg["name"] == "my-app"
    # The CLI/server supply the instance's SDK at runtime/build time; generated
    # source must not persist the creating instance.
    assert "bifrost" not in pkg["dependencies"]
    assert "inst.example" not in files["package.json"]
    assert "react" in pkg["dependencies"]
    assert "lucide-react" in pkg["dependencies"]
    assert pkg["scripts"]["dev"] == "vite"

    # vite.config reads process/exact-directory overrides, then uses the CLI's
    # selected default profile, so `npm run dev` authenticates with no pasting.
    vc = files["vite.config.ts"]
    assert "BIFROST_ACCESS_TOKEN" in vc
    assert "VITE_BIFROST_TOKEN" in vc
    assert "process.env.BIFROST_ACCESS_TOKEN" in vc  # env first
    assert 'join(process.cwd(), ".env")' in vc
    assert "dirname" not in vc
    assert "(BIFROST_API_URL|BIFROST_ACCESS_TOKEN)" not in vc
    assert "BIFROST_ACCESS_TOKEN" not in files[".env.example"]
    # Tokens live in the keyring / credentials.json, so the config must fall
    # back to the CLI store via `bifrost auth token`.
    assert "auth" in vc and "token" in vc
    assert "execFileSync" in vc
    assert "if (!out.token)" in vc
    assert 'const args = ["auth", "token"]' in vc
    assert 'if (out.url) args.push("--url", out.url)' in vc
    assert 'if (creds.api_url && !out.url) out.url = creds.api_url' in vc
    # SECURITY (Codex R6-P1-c): the token is injected ONLY for `vite` serve
    # (dev), never for `vite build` — baking it into the production bundle would
    # leak a usable credential to every app user. The config must gate `define`
    # on the build command.
    assert 'command === "serve"' in vc

    # The README must NOT tell the developer to paste a token.
    assert "paste" not in files["README.md"].lower()
    assert "selected default profile" in files["README.md"].lower()

    # main.tsx follows the explicit lifecycle contract: the immutable module
    # registers mount(), receives bootstrap directly, and returns teardown.
    main = files["src/main.tsx"]
    index_html = files["index.html"]
    assert 'name="bifrost-app-runtime" content="mount-v1"' in index_html
    assert '<html lang="en" class="h-full">' in index_html
    assert '<body class="h-full">' in index_html
    assert '<div id="root" class="h-full"></div>' in index_html
    assert "createRoot" in main
    assert "export function mount" in main
    assert "__BIFROST_APP_MODULES__" in main
    assert "return () => root.unmount()" in main
    assert "VITE_BIFROST_TOKEN" in main
    assert "BrowserRouter basename={bootstrap.basename}" in main
    assert "solutionId={bootstrap.solutionId}" in main
    assert "if (import.meta.env.DEV)" in main
    assert "import.meta.url" in main
    assert "__BIFROST_APP__" not in main
    assert "registerUnmount" not in main
    assert 'searchParams.get("m")' not in main

    # App.tsx composes the optional platform header.
    app = files["src/App.tsx"]
    assert "BifrostHeader" in app


def test_scaffold_app_ships_top_nav_shell() -> None:
    """App.tsx is the navigation shell new Apps copy: one header carrying the
    NAV list (rendered with the router's NavLink, collapsed into its menu on
    phones), `main` as the only scroller, nested routes with a catch-all, and
    semantic token classes instead of inline styles."""
    app = _v2_scaffold_files("my-app")["src/App.tsx"]

    assert app.count("<BifrostHeader") == 1
    assert "const NAV = [" in app
    assert "nav={{ items: NAV, link: NavLink }}" in app
    # The header owns the nav; the shell must not render a second nav row.
    assert "<nav" not in app
    assert 'className="flex h-full min-h-0 flex-col' in app
    assert '<main className="min-h-0 flex-1 overflow-auto">' in app
    assert "<Outlet />" in app
    assert "<Route element={<Shell />}>" in app
    assert 'path="*"' in app
    # App.tsx must not import UI components the scaffold doesn't ship.
    assert "@/components/" not in app
    assert "style={{" not in app
    assert "crimson" not in app


def test_scaffold_app_starter_button_is_an_unwired_placeholder() -> None:
    """The starter button calls no workflow: a fresh App or Solution has no
    workflow to call, so a default ref would fail on first click. The comment
    beside it shows how to wire it with the workflow hooks."""
    app = _v2_scaffold_files("my-app")["src/App.tsx"]

    code = "\n".join(
        line for line in app.splitlines() if not line.lstrip().startswith("//")
    )
    assert "Run workflow" in code
    assert "useWorkflow" not in code
    assert "onClick" not in code
    assert "hello.py" not in app
    # The wiring guidance names both hooks and the mutation pattern.
    assert "useWorkflowQuery(ref)" in app
    assert "useWorkflowMutation(ref)" in app
    assert "wf.mutate({})" in app


def test_scaffold_ships_tailwind_v4_shadcn_and_theme() -> None:
    """A v2 app with no Tailwind renders UNSTYLED — so the scaffold ships Tailwind
    v4 + the shadcn token layer + theme wiring by DEFAULT (this is the fix for the
    migrated-app 'unstyled gray box, no dark toggle' regression)."""
    files = _v2_scaffold_files("my-app")
    pkg = json.loads(files["package.json"])

    # Tailwind v4 via the vite plugin + the shadcn cn() deps.
    assert "tailwindcss" in pkg["devDependencies"]
    assert "@tailwindcss/vite" in pkg["devDependencies"]
    for dep in ("clsx", "tailwind-merge", "class-variance-authority"):
        assert dep in pkg["dependencies"], f"{dep} missing (shadcn needs it)"

    # vite wires the tailwind plugin + the `@/` alias shadcn source imports use.
    vc = files["vite.config.ts"]
    assert "tailwindcss()" in vc
    assert '"@"' in vc and "alias" in vc

    # The CSS imports tailwind, defines the shadcn tokens, AND the `.dark` layer
    # the BifrostProvider toggles — without `.dark` tokens the dark toggle does
    # nothing.
    css = files["src/index.css"]
    assert '@import "tailwindcss"' in css
    assert ".dark" in css
    assert "--radius" in css           # rounded corners come from this token
    assert "custom-variant dark" in css

    # components.json so `npx shadcn add <component>` drops REAL current source —
    # and it MIRRORS THE PLATFORM (radix-rhea style, not new-york) so migrated
    # apps look native: the rhea style is more rounded + matches the platform.
    cfg = json.loads(files["components.json"])
    assert cfg["tailwind"]["css"] == "src/index.css"
    assert cfg["aliases"]["ui"] == "@/components/ui"
    assert cfg["style"] == "radix-rhea"

    # Platform-matching tokens: teal brand primary + the Rhea (0.65rem,
    # multiplicative) radius scale, not generic new-york neutral/0.625.
    assert "0.65rem" in css
    assert "Teal brand" in css
    assert "--radius-4xl" in css       # the rhea scale extends to 4xl
    assert '@import "tw-animate-css"' in css
    assert "tw-animate-css" in pkg["devDependencies"]

    # cn() helper for shadcn components.
    assert "twMerge" in files["src/lib/utils.ts"]

    # Theme is ON by default: supportsTheme makes BifrostHeader show the toggle.
    main = files["src/main.tsx"]
    assert "supportsTheme" in main
    assert 'import "./index.css"' in main


def test_scaffold_app_nested_path_anchors_manifests_at_root(tmp_path, monkeypatch) -> None:
    # With a nested --path, the .bifrost/ manifests must land at the DESCRIPTOR
    # root (not app_dir.parent.parent), and the manifest path entry must be a
    # POSIX root-relative path (so _app_source_dirs' POSIX comparisons match).
    _init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        solution_group,
        ["scaffold-app", "dash", "--path", "src/apps/dash"],
    )
    assert result.exit_code == 0, result.output

    # Manifests at the root — and no stray src/.bifrost.
    assert (tmp_path / ".bifrost" / "apps.yaml").is_file()
    assert not (tmp_path / "src" / ".bifrost").exists()
    # App files at the nested path.
    assert (tmp_path / "src" / "apps" / "dash" / "package.json").is_file()

    data = yaml.safe_load((tmp_path / ".bifrost" / "apps.yaml").read_text())
    (entry,) = data["apps"].values()
    assert entry["path"] == "src/apps/dash"


def test_scaffold_app_path_outside_workspace_refuses(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    _init_workspace(root)
    monkeypatch.chdir(root)

    result = CliRunner().invoke(
        solution_group,
        ["scaffold-app", "dash", "--path", "../elsewhere/dash"],
    )
    assert result.exit_code != 0
    assert "inside the solution workspace" in result.output
    # Nothing written — not the escape dir, not manifests.
    assert not (tmp_path / "elsewhere").exists()
    assert not (root / ".bifrost").exists()
    assert not (root / "functions").exists()


def test_scaffold_app_refuses_outside_solution_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no bifrost.solution.yaml anywhere up the tree
    result = CliRunner().invoke(solution_group, ["scaffold-app", "dash"])
    assert result.exit_code != 0
    assert "solution init" in result.output
    assert not (tmp_path / ".bifrost").exists()
