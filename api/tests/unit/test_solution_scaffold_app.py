"""`bifrost solution scaffold-app` writes a working standalone_v2 skeleton with
the CLI-login dev loop wired in — no token pasting (Codex R4 DX)."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.solution import _v2_scaffold_files  # noqa: E402


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
    # SECURITY (Codex R6-P1-c): the token is injected ONLY for `vite` serve
    # (dev), never for `vite build` — baking it into the production bundle would
    # leak a usable credential to every app user. The config must gate `define`
    # on the build command.
    assert 'command === "serve"' in vc

    # The README must NOT tell the developer to paste a token.
    assert "paste" not in files["README.md"].lower()

    # main.tsx follows the runtime contract: reads window.__BIFROST_APP__,
    # createRoot, registers unmount, falls back to the dev env.
    main = files["src/main.tsx"]
    assert "window.__BIFROST_APP__" in main
    assert "createRoot" in main
    assert "registerUnmount" in main
    assert "VITE_BIFROST_TOKEN" in main
    assert "BrowserRouter basename" in main

    # App.tsx composes the optional platform header + shows a workflow call.
    app = files["src/App.tsx"]
    assert "BifrostHeader" in app
    assert "useWorkflow" in app
