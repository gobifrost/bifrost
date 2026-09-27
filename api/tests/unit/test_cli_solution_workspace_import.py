"""CLI decisions for reviewed workspace bundle imports."""

from unittest.mock import MagicMock, patch


def _preview() -> dict:
    return {"items": [
        {"id": "file:a.py", "kind": "file", "name": "a.py", "classification": "conflict"},
        {"id": "file:b.py", "kind": "file", "name": "b.py", "classification": "conflict"},
    ]}


def test_workspace_import_bulk_keep_selects_every_conflict() -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    assert _workspace_import_decisions(_preview(), keep_all=True, replace_all=False, decisions_path=None, json_output=False) == [
        {"item_id": "file:a.py", "action": "keep"},
        {"item_id": "file:b.py", "action": "keep"},
    ]


def test_workspace_import_interactive_replace_all_applies_remaining() -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    stream = MagicMock()
    stream.isatty.return_value = True
    with (
        patch("bifrost.commands.solution.click.prompt", return_value="R"),
        patch("bifrost.commands.solution.click.get_text_stream", return_value=stream),
    ):
        assert _workspace_import_decisions(_preview(), keep_all=False, replace_all=False, decisions_path=None, json_output=False) == [
            {"item_id": "file:a.py", "action": "replace"},
            {"item_id": "file:b.py", "action": "replace"},
        ]
