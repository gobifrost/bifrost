from bifrost.commands.solution import (
    _SAMPLE_WORKFLOW_PATH,
    _SAMPLE_WORKFLOW_REF,
    _SAMPLE_WORKFLOW_SOURCE,
    _v2_scaffold_files,
)


def test_main_tsx_has_vite_app_id_fallback():
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    main = files["src/main.tsx"]
    assert "import.meta.env.VITE_BIFROST_APP_ID" in main
    assert "import.meta.env.VITE_BIFROST_ORG_ID" in main


def test_vite_config_injects_app_id_and_org_on_serve():
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    vite = files["vite.config.ts"]
    assert "VITE_BIFROST_APP_ID" in vite
    assert "VITE_BIFROST_ORG_ID" in vite


def test_sample_workflow_ref_matches_app_tsx():
    # The App.tsx ref must equal the sample workflow ref so the first-run button
    # resolves the sample. The sample SOURCE is written at the solution root by
    # scaffold_app_cmd (not in the app-relative file dict), so it is NOT in
    # _v2_scaffold_files — that placement (root vs app dir) is what makes the
    # root-relative ref resolve under `solution start`.
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    app = files["src/App.tsx"]
    assert "useWorkflow" in app
    assert _SAMPLE_WORKFLOW_REF in app
    assert _SAMPLE_WORKFLOW_REF == "functions/hello.py::main"
    assert _SAMPLE_WORKFLOW_PATH == "functions/hello.py"
    assert "def main" in _SAMPLE_WORKFLOW_SOURCE
    # The sample is NOT bundled into the app dir (it lives at the solution root).
    assert "functions/hello.py" not in files


def test_scaffold_app_writes_sample_at_solution_root(tmp_path, monkeypatch):
    # The bug a live drive caught: the sample must land at <solution-root>/
    # functions/hello.py, NOT apps/<slug>/functions/hello.py — otherwise the
    # root-relative ref `functions/hello.py::main` never resolves under
    # `solution start` (discovery keys an app-dir copy as
    # `apps/<slug>/functions/hello.py::main`).
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        solution_group, ["scaffold-app", "dashboard", "--api-url", "http://localhost:8000"]
    )
    assert result.exit_code == 0, result.output
    # The sample is at the solution root, runnable, and matches the App.tsx ref.
    root_sample = tmp_path / "functions" / "hello.py"
    assert root_sample.is_file()
    assert "def main" in root_sample.read_text()
    # It is NOT inside the app dir.
    assert not (tmp_path / "apps" / "dashboard" / "functions" / "hello.py").exists()
