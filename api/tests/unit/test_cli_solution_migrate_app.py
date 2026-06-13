"""`bifrost solution migrate-app` — deterministic 80% of a v1→v2 app migration.

Scaffolds the v2 skeleton, ports v1 pages/components, runs the deterministic
--v2 import rewrite, and PRINTS a judgment checklist (multi-route wiring,
unresolved imports, no-v2 hooks, cutover order) — it STOPS before build/wire so
nothing judgment-heavy is silently done. The npm/npx (shadcn) calls are mocked;
the test pins the deterministic file + checklist behavior.
"""
from __future__ import annotations

import pathlib
from unittest import mock

from click.testing import CliRunner

from bifrost.commands.solution import solution_group


def _v1_app(tmp: pathlib.Path) -> pathlib.Path:
    """A minimal v1 inline app: pages/ + components/ importing from bifrost."""
    app = tmp / "v1src"
    (app / "pages").mkdir(parents=True)
    (app / "components").mkdir(parents=True)
    (app / "pages" / "index.tsx").write_text(
        'import { Button, Card, Link, useState, useWorkflowQuery } from "bifrost";\n'
        'export default function X() { return null; }\n'
    )
    (app / "components" / "Dlg.tsx").write_text(
        'import { Dialog, DialogContent, useUser } from "bifrost";\n'
        'export const Dlg = () => null;\n'
    )
    (app / "app.yaml").write_text("name: v1\n")  # non-standard entry → reported
    return app


def _run(tmp: pathlib.Path, source: pathlib.Path):
    # Make tmp a solution workspace root.
    (tmp / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    runner = CliRunner()
    # Mock the network-bound shadcn/npm steps; run from the workspace root.
    with mock.patch("subprocess.run") as sp:
        sp.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with runner.isolated_filesystem(temp_dir=tmp):
            import os
            os.chdir(tmp)
            return runner.invoke(
                solution_group,
                ["migrate-app", str(source), "csp-v2", "--title", "CSP",
                 "--api-url", "http://x"],
            )


def test_migrate_app_ports_rewrites_and_prints_checklist(tmp_path):
    src = _v1_app(tmp_path)
    result = _run(tmp_path, src)
    assert result.exit_code == 0, result.output

    app = tmp_path / "apps" / "csp-v2"
    # Ported the v1 source into src/.
    assert (app / "src" / "pages" / "index.tsx").is_file()
    assert (app / "src" / "components" / "Dlg.tsx").is_file()

    # Deterministic import rewrite happened on the ported page.
    idx = (app / "src" / "pages" / "index.tsx").read_text()
    assert 'from "react"' in idx and "useState" in idx
    assert 'from "react-router-dom"' in idx and "Link" in idx
    assert 'from "@/components/ui/button"' in idx
    assert 'from "@/components/ui/card"' in idx
    # hooks stay in bifrost
    assert 'useWorkflowQuery } from "bifrost"' in idx

    # Checklist surfaced the judgment items, not hidden:
    out = result.output
    assert "migrate-app stops here" in out.lower() or "stops here" in out.lower()
    assert "useUser" in out                 # no-v2-equivalent hook flagged
    assert "swap-slugs" in out              # cutover step
    assert "capture is terminal" in out     # the ordering trap
    assert "app.yaml" in out or "non-standard" in out  # unexpected entry reported


def test_migrate_app_never_touches_components_ui(tmp_path):
    src = _v1_app(tmp_path)
    # Pre-seed a shadcn-style ui file in the SOURCE to ensure it's not rewritten.
    (src / "components" / "ui").mkdir(parents=True)
    (src / "components" / "ui" / "button.tsx").write_text(
        'import { Slot } from "radix-ui";\nexport const Button = () => null;\n'
    )
    result = _run(tmp_path, src)
    assert result.exit_code == 0, result.output
    ui = tmp_path / "apps" / "csp-v2" / "src" / "components" / "ui" / "button.tsx"
    if ui.is_file():
        # If ported, it must be untouched (still imports radix-ui, NOT bifrost).
        assert 'from "radix-ui"' in ui.read_text()
        assert 'from "bifrost"' not in ui.read_text()
