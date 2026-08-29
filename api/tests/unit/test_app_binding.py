from pathlib import Path

from bifrost.app_binding import AppBinding, find_app_root, read_app_binding, write_app_binding


def test_standard_env_binding_preserves_unrelated_values(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".env").write_text("CUSTOM=value\nBIFROST_APP_ID=old\n")

    write_app_binding(tmp_path, AppBinding("https://example.test/", "new-id"))

    assert read_app_binding(tmp_path) == AppBinding("https://example.test", "new-id")
    assert "CUSTOM=value" in (tmp_path / ".env").read_text()
    child = tmp_path / "src" / "nested"
    child.mkdir(parents=True)
    assert find_app_root(child) == tmp_path
