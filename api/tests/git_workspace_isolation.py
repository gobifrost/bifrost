"""Isolate test checkout trees together with their external state receipts."""

import shutil
from contextlib import contextmanager
from pathlib import Path

from src.services.git_workspace_state import GitWorkspaceState


def _empty_directory(path: Path) -> None:
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


@contextmanager
def isolated_git_workspaces(checkouts: list[Path], backup: Path):
    receipts = [GitWorkspaceState._receipt_path(checkout) for checkout in checkouts]
    for checkout in checkouts:
        assert checkout.is_dir(), f"missing test-harness Git mount: {checkout}"
        shutil.copytree(checkout, backup / checkout.name, symlinks=True)
    for receipt in receipts:
        if receipt.is_file():
            shutil.copy2(receipt, backup / receipt.name, follow_symlinks=False)
    try:
        for checkout in checkouts:
            _empty_directory(checkout)
        for receipt in receipts:
            receipt.unlink(missing_ok=True)
        yield
    finally:
        for checkout in checkouts:
            _empty_directory(checkout)
            shutil.copytree(backup / checkout.name, checkout, dirs_exist_ok=True, symlinks=True)
        for receipt in receipts:
            receipt.unlink(missing_ok=True)
            saved = backup / receipt.name
            if saved.is_file():
                shutil.copy2(saved, receipt, follow_symlinks=False)
