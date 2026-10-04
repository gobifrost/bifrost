from bifrost.commands.solution import _v2_scaffold_files


def test_main_tsx_has_vite_app_id_fallback():
    files = _v2_scaffold_files("dash")
    main = files["src/main.tsx"]
    assert "import.meta.env.VITE_BIFROST_APP_ID" in main
    assert "import.meta.env.VITE_BIFROST_ORG_ID" in main


def test_vite_config_injects_app_id_and_org_on_serve():
    files = _v2_scaffold_files("dash")
    vite = files["vite.config.ts"]
    assert "VITE_BIFROST_APP_ID" in vite
    assert "VITE_BIFROST_ORG_ID" in vite


def test_scaffold_app_writes_no_sample_workflow(tmp_path, monkeypatch):
    # The starter button is an unwired placeholder, so scaffold-app writes no
    # workflow source and no workflows.yaml entry: nothing would call them, and
    # deploy would ship an unused workflow into every install.
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    monkeypatch.chdir(tmp_path)
    # scaffold-app anchors at the descriptor root, so the workspace must exist.
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    result = CliRunner().invoke(
        solution_group, ["scaffold-app", "dashboard"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".bifrost" / "apps.yaml").is_file()
    assert not (tmp_path / ".bifrost" / "workflows.yaml").exists()
    assert not (tmp_path / "functions").exists()
    assert not any(p.suffix == ".py" for p in tmp_path.rglob("*"))
