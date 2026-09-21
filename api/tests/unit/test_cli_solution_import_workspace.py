"""Safety checks for the two-phase workspace-import CLI command."""
from __future__ import annotations

import pytest


def test_noninteractive_conflicts_require_explicit_decisions(monkeypatch) -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    class Stdin:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("click.get_text_stream", lambda _name: Stdin())
    with pytest.raises(Exception, match="Use --keep-all, --replace-all, or --decisions"):
        _workspace_import_decisions(
            {"items": [{"id": "file:a.py", "classification": "conflict"}]},
            keep_all=False, replace_all=False, decisions_path=None, json_output=False,
        )


def test_replace_all_covers_each_conflict() -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    assert _workspace_import_decisions(
        {"items": [
            {"id": "file:a.py", "classification": "conflict"},
            {"id": "entity:workflow:w", "classification": "conflict"},
        ]},
        keep_all=False, replace_all=True, decisions_path=None, json_output=True,
    ) == [
        {"item_id": "file:a.py", "action": "replace"},
        {"item_id": "entity:workflow:w", "action": "replace"},
    ]
