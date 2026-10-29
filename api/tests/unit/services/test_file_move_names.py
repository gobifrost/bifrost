"""Windows-style rename candidates for files.move on_conflict='rename'."""

import pytest

from src.services.file_storage.move_names import (
    candidate_prefix,
    matching_candidates,
    numbered_name,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a/report.pdf", "a/report (2).pdf"),
        ("report", "report (2)"),
        ("a/.env", "a/.env (2)"),
        ("a/a.tar.gz", "a/a.tar (2).gz"),
        ("a/Floor Plan – Rev 2 (final).pdf", "a/Floor Plan – Rev 2 (final) (2).pdf"),
    ],
)
def test_numbered_name(path: str, expected: str) -> None:
    assert numbered_name(path, 2) == expected


def test_candidate_prefix() -> None:
    assert candidate_prefix("a/b/report.pdf") == "a/b/report ("
    assert candidate_prefix("report.pdf") == "report ("


def test_matching_candidates_keeps_only_exact_numbered_siblings() -> None:
    names = [
        "report (1).pdf",
        "report (3).pdf",
        "report (draft).pdf",
        "report (0).pdf",
        "report (01).pdf",
        "report (2).txt",
        "report (4).pdf.bak",
    ]
    assert matching_candidates("x/report.pdf", names) == {
        1: "report (1).pdf",
        3: "report (3).pdf",
    }


def test_matching_candidates_escapes_regex_characters() -> None:
    names = ["Floor Plan – Rev 2 (final) (1).pdf", "Floor Plan – Rev 2 (final)X(1).pdf"]
    assert matching_candidates("Floor Plan – Rev 2 (final).pdf", names) == {
        1: "Floor Plan – Rev 2 (final) (1).pdf"
    }
