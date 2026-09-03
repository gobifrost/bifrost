"""Segment-aware path containment for claim-backed policies."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from shared.path_matching import path_matches_prefix, path_within_any
from shared.policies.evaluate import evaluate
from src.models.contracts.policies import Expr, FileExpr


@pytest.mark.parametrize(
    ("path", "prefixes", "expected"),
    [
        ("site-a:category-1/pdf", ["site-a:category-1/pdf"], True),
        ("site-a:category-1/pdf/doc-1/file.pdf", ["site-a:category-1/pdf"], True),
        ("/site-a:category-1/pdf/doc-1/", ["/site-a:category-1/pdf/"], True),
        ("site-annex:category-1/pdf/doc-1/file.pdf", ["site-a"], False),
        ("site-a:category-1/pdf2/doc-1/file.pdf", ["site-a:category-1/pdf"], False),
        ("anything", ["", "/", None, 7], False),
        ("anything", [], False),
        ("anything", "anything", False),
        (None, ["anything"], False),
    ],
)
def test_path_within_any_is_segment_aware_and_fails_closed(
    path: object, prefixes: object, expected: bool
) -> None:
    assert path_within_any(path, prefixes) is expected


def test_root_prefix_is_reserved_for_policy_selection() -> None:
    assert path_matches_prefix("", "any/path") is True
    assert path_within_any("any/path", [""]) is False


def test_contract_accepts_claim_rhs_only_for_file_expressions() -> None:
    with pytest.raises(ValidationError, match="unknown operator 'path_within_any'"):
        Expr.model_validate(
            {
                "path_within_any": [
                    {"row": "resource_path"},
                    {"claims": "allowed_resource_paths"},
                ]
            }
        )

    FileExpr.model_validate(
        {
            "path_within_any": [
                {"file": "path"},
                {"claims": "allowed_resource_paths"},
            ]
        }
    )


def test_contract_rejects_non_string_literal_prefix() -> None:
    with pytest.raises(ValidationError, match="must be strings"):
        FileExpr.model_validate({"path_within_any": [{"file": "path"}, ["valid", 7]]})


def test_evaluator_uses_claim_backed_prefixes() -> None:
    user = SimpleNamespace(claims={"allowed_resource_paths": ["site-a:category-1/pdf"]})
    expr = FileExpr.model_validate(
        {
            "path_within_any": [
                {"file": "path"},
                {"claims": "allowed_resource_paths"},
            ]
        }
    )

    assert (
        evaluate(
            expr,
            {"path": "site-a:category-1/pdf/doc-1/file.pdf"},
            user,
            resolvers={
                "file": lambda field: {
                    "path": "site-a:category-1/pdf/doc-1/file.pdf"
                }.get(field)
            },
        )
        is True
    )
    assert (
        evaluate(
            expr,
            {"path": "site-a:category-1/download/doc-1/file.dwg"},
            user,
            resolvers={
                "file": lambda field: {
                    "path": "site-a:category-1/download/doc-1/file.dwg"
                }.get(field)
            },
        )
        is False
    )
