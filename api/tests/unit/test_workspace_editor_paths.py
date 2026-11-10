"""Canonical validation rules for workspace editor paths."""

import pytest

from src.services.editor.file_filter import (
    normalize_workspace_delete_path,
    normalize_workspace_folder_path,
    normalize_workspace_file_path,
    normalize_workspace_list_prefix,
)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "//",
        ".",
        "..",
        "folder/./file.txt",
        "folder/../file.txt",
        "folder//file.txt",
        "folder//",
        "folder\\file.txt",
        "C:/workspace/file.txt",
        "C:workspace/file.txt",
        "folder\x00/file.txt",
        ".git/config",
        "folder/.env",
    ],
)
def test_folder_and_delete_paths_reject_ambiguous_or_excluded_spellings(path):
    with pytest.raises(ValueError):
        normalize_workspace_delete_path(path)
    with pytest.raises(ValueError):
        normalize_workspace_folder_path(path)
    with pytest.raises(ValueError):
        normalize_workspace_file_path(path)


def test_list_prefix_alone_accepts_workspace_root():
    assert normalize_workspace_list_prefix(None) == ""
    assert normalize_workspace_list_prefix("") == ""
    assert normalize_workspace_list_prefix("/") == ""


def test_valid_workspace_paths_preserve_manifest_spaces_unicode_and_encoded_characters():
    assert normalize_workspace_delete_path(".bifrost/forms.yaml") == ".bifrost/forms.yaml"
    assert normalize_workspace_folder_path("Team Notes/équipe/") == "Team Notes/équipe"
    assert normalize_workspace_delete_path("safe/%2e%2e.txt") == "safe/%2e%2e.txt"
    assert normalize_workspace_list_prefix("Team Notes/équipe/") == "Team Notes/équipe/"


def test_file_targets_reject_folder_slash_and_keep_ordinary_names():
    with pytest.raises(ValueError):
        normalize_workspace_file_path("folder/")
    assert normalize_workspace_file_path("Team Notes/équipe.txt") == "Team Notes/équipe.txt"
