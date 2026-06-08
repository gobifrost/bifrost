from bifrost.commands.solution import _v2_scaffold_files


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


def test_sample_function_shipped_and_ref_matches_app_tsx():
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    assert "functions/hello.py" in files
    app = files["src/App.tsx"]
    assert "useWorkflow" in app
    assert "functions/hello.py::main" in app
    assert "def main" in files["functions/hello.py"]
