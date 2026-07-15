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
    files = _v2_scaffold_files("my-app", "https://inst.example")

    # All the files a normal Vite app needs.
    for f in ("package.json", "vite.config.ts", "index.html", "src/main.tsx",
              "src/App.tsx", ".env.example", "README.md"):
        assert f in files, f"{f} not scaffolded"

    pkg = json.loads(files["package.json"])
    assert pkg["name"] == "my-app"
    # `bifrost` resolves FROM THE INSTANCE (no public npm, no pasting).
    assert pkg["dependencies"]["bifrost"] == "https://inst.example/api/sdk/download"
    assert "react" in pkg["dependencies"]
    assert "lucide-react" in pkg["dependencies"]
    assert pkg["scripts"]["dev"] == "vite"

    # vite.config reads the CLI's own token (env OR the nearest .env up the
    # tree), so `npm run dev` authenticates with NO token pasting.
    vc = files["vite.config.ts"]
    assert "BIFROST_ACCESS_TOKEN" in vc
    assert "VITE_BIFROST_TOKEN" in vc
    assert "process.env.BIFROST_ACCESS_TOKEN" in vc  # env first
    assert "dirname" in vc  # walks up to find the .env
    # R7-P2-f: device-code login stores the token in the keyring / credentials.json
    # (not a .env), so the config must fall back to the CLI credential store via
    # `bifrost auth token` — otherwise the normal login path starts dev tokenless.
    assert "auth" in vc and "token" in vc
    assert "execFileSync" in vc
    # SECURITY (Codex R6-P1-c): the token is injected ONLY for `vite` serve
    # (dev), never for `vite build` — baking it into the production bundle would
    # leak a usable credential to every app user. The config must gate `define`
    # on the build command.
    assert 'command === "serve"' in vc

    # The README must NOT tell the developer to paste a token.
    assert "paste" not in files["README.md"].lower()

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
    assert "if (import.meta.env.DEV)" in main
    assert "import.meta.url" in main
    assert "__BIFROST_APP__" not in main
    assert "registerUnmount" not in main
    assert 'searchParams.get("m")' not in main

    # App.tsx composes the optional platform header + shows a workflow call.
    app = files["src/App.tsx"]
    assert "BifrostHeader" in app
    assert "useWorkflow" in app


def test_scaffold_ships_tailwind_v4_shadcn_and_theme() -> None:
    """A v2 app with no Tailwind renders UNSTYLED — so the scaffold ships Tailwind
    v4 + the shadcn token layer + theme wiring by DEFAULT (this is the fix for the
    migrated-app 'unstyled gray box, no dark toggle' regression)."""
    files = _v2_scaffold_files("my-app", "https://inst.example")
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
        ["scaffold-app", "dash", "--path", "src/apps/dash", "--api-url", "http://localhost:8000"],
    )
    assert result.exit_code == 0, result.output

    # Manifests at the root — and no stray src/.bifrost.
    assert (tmp_path / ".bifrost" / "apps.yaml").is_file()
    assert (tmp_path / ".bifrost" / "workflows.yaml").is_file()
    assert not (tmp_path / "src" / ".bifrost").exists()
    # Sample workflow at the root, app files at the nested path.
    assert (tmp_path / "functions" / "hello.py").is_file()
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
        ["scaffold-app", "dash", "--path", "../elsewhere/dash", "--api-url", "http://localhost:8000"],
    )
    assert result.exit_code != 0
    assert "inside the solution workspace" in result.output
    # Nothing written — not the escape dir, not manifests.
    assert not (tmp_path / "elsewhere").exists()
    assert not (root / ".bifrost").exists()
    assert not (root / "functions").exists()


def test_scaffold_app_refuses_outside_solution_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no bifrost.solution.yaml anywhere up the tree
    result = CliRunner().invoke(
        solution_group, ["scaffold-app", "dash", "--api-url", "http://localhost:8000"]
    )
    assert result.exit_code != 0
    assert "solution init" in result.output
    assert not (tmp_path / ".bifrost").exists()
