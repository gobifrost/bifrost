"""Conformance coverage for the Cloudflare-derived sandbox catalog ranking."""

import json
from pathlib import Path

from src.services.capabilities.search import (
    SEARCH_RESULT_LIMIT,
    CapabilitySearchItem,
    search_capabilities,
)


_CLOUDFLARE_FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "agent_runtime" / "cloudflare_search.json"
)


def test_search_matches_upstream_cloudflare_golden_cases() -> None:
    fixture = json.loads(_CLOUDFLARE_FIXTURE.read_text())
    assert fixture["upstream"]["commit"] == "321814b6c13f72cff82161e7eb20312574ac9b7a"
    assert fixture["upstream"]["sha256"] == (
        "15ef317d630b3df60e6cc48bb879d835d2a4e968214f0b4096265504364981e0"
    )
    items = [
        CapabilitySearchItem(
            path=f"{description['name']}.{method}",
            connector=description["name"],
            method=method,
            description=descriptor.get("description"),
        )
        for description in fixture["descriptions"]
        for method, descriptor in description["descriptors"].items()
    ]

    for case in fixture["cases"]:
        assert search_capabilities(case["query"], items) == case["expected"]


def test_search_matches_cloudflare_field_weights_and_token_coverage() -> None:
    result = search_capabilities(
        "github issues",
        [
            CapabilitySearchItem(
                path="github_list_issues",
                connector="github",
                method="listIssues",
                description="List open repository issues",
            ),
            CapabilitySearchItem(
                path="github_create_issue",
                connector="github",
                method="createIssue",
                description="Create a new repository issue",
            ),
            CapabilitySearchItem(
                path="linear_search_issues",
                connector="linear",
                method="searchIssues",
                description="Search tracked issues",
            ),
        ],
    )

    assert result == {
        "results": [
            {
                "path": "github_list_issues",
                "connector": "github",
                "method": "listIssues",
                "description": "List open repository issues",
                "kind": "method",
                "score": 221,
            },
            {
                "path": "github_create_issue",
                "connector": "github",
                "method": "createIssue",
                "description": "Create a new repository issue",
                "kind": "method",
                "score": 167,
            },
        ],
        "total": 2,
        "truncated": False,
    }


def test_search_limits_ranked_results_to_cloudflare_limit() -> None:
    result = search_capabilities(
        "record",
        [
            CapabilitySearchItem(
                path=f"read_record_{index:02d}",
                connector="records",
                method=f"readRecord{index:02d}",
            )
            for index in range(SEARCH_RESULT_LIMIT + 1)
        ],
    )

    assert result["total"] == SEARCH_RESULT_LIMIT + 1
    assert result["truncated"] is True
    assert [item["path"] for item in result["results"]] == [
        f"read_record_{index:02d}" for index in range(SEARCH_RESULT_LIMIT)
    ]
