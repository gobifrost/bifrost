"""Prompt assertion-catalog contract for the Test Designer."""

from src.services.agent_evaluations.assertions import (
    KNOWN_ASSERTION_TYPES,
    validate_assertions,
)
from src.services.agent_evaluations.test_designer import (
    DETERMINISTIC_ASSERTION_CATALOG,
    build_designer_input,
)


def test_designer_input_includes_every_deterministic_assertion_with_valid_example():
    catalog_types = {entry["type"] for entry in DETERMINISTIC_ASSERTION_CATALOG}

    assert catalog_types == KNOWN_ASSERTION_TYPES - {"llm_judge"}
    assert all(entry["guidance"] for entry in DETERMINISTIC_ASSERTION_CATALOG)
    validate_assertions(
        [
            {"type": entry["type"], "params": entry["params"]}
            for entry in DETERMINISTIC_ASSERTION_CATALOG
        ]
    )

    designer_input = build_designer_input(
        agent_snapshot={},
        tool_schemas={},
        suite_goal="cover ticket lookup",
        requested_count=1,
    )

    assert designer_input["assertion_catalog"] == list(DETERMINISTIC_ASSERTION_CATALOG)
    assert all(
        entry["type"] != "llm_judge"
        for entry in designer_input["assertion_catalog"]
    )
