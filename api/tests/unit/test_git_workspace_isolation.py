from pathlib import Path

import pytest

from src.services.git_workspace_state import GitWorkspaceState
from tests.git_workspace_isolation import isolated_git_workspaces


@pytest.mark.parametrize("has_receipt", [False, True])
def test_checkout_and_receipt_are_isolated_and_restored_on_failure(tmp_path: Path, has_receipt: bool):
    checkouts = [tmp_path / name for name in ("worker", "scheduler")]
    backup = tmp_path / "backup"
    backup.mkdir()
    for checkout in checkouts:
        checkout.mkdir()
        (checkout / "original.py").write_text(checkout.name)
        if has_receipt:
            GitWorkspaceState._receipt_path(checkout).write_text("original receipt")

    caught = []
    try:
        with isolated_git_workspaces(checkouts, backup):
            for checkout in checkouts:
                assert list(checkout.iterdir()) == []
                assert not GitWorkspaceState._receipt_path(checkout).exists()
                (checkout / "temporary.py").write_text("temporary")
                GitWorkspaceState._receipt_path(checkout).write_text("new receipt")
            raise RuntimeError("exercise cleanup")
    except RuntimeError as error:
        assert str(error) == "exercise cleanup"
        caught.append(error)
    assert len(caught) == 1

    for checkout in checkouts:
        assert sorted(path.name for path in checkout.iterdir()) == ["original.py"]
        assert (checkout / "original.py").read_text() == checkout.name
        receipt = GitWorkspaceState._receipt_path(checkout)
        if has_receipt:
            assert receipt.read_text() == "original receipt"
        else:
            assert not receipt.exists()
