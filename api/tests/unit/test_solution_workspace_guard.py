import pathlib

import pytest

from bifrost._solution_workspace import (
    assert_not_solution_workspace,
    find_solution_root,
)


def test_find_solution_root_walks_up(tmp_path):
    (tmp_path / "bifrost.solution.yaml").write_text("name: demo\n")
    sub = tmp_path / "functions" / "deep"
    sub.mkdir(parents=True)
    assert find_solution_root(sub) == tmp_path


def test_no_solution_root(tmp_path):
    assert find_solution_root(tmp_path) is None


def test_assert_blocks_with_message(tmp_path, capsys):
    (tmp_path / "bifrost.solution.yaml").write_text("name: demo\n")
    with pytest.raises(SystemExit):
        assert_not_solution_workspace(str(tmp_path), "push")
    err = capsys.readouterr().err
    assert "Solution workspace" in err and "solution deploy" in err


def test_assert_passes_outside_solution(tmp_path):
    # No descriptor anywhere up the tree -> no raise.
    assert_not_solution_workspace(str(tmp_path), "push") is None


def test_handle_push_blocks_in_solution(tmp_path, monkeypatch):
    (tmp_path / "bifrost.solution.yaml").write_text("name: demo\n")
    f = tmp_path / "x.py"
    f.write_text("x=1\n")
    monkeypatch.chdir(tmp_path)
    from bifrost import cli

    with pytest.raises(SystemExit):
        cli.handle_push(["x.py"])
