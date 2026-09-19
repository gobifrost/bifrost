"""Quota tests: Studio bounds fail closed before persistence."""

from __future__ import annotations

import pytest

from src.services.agent_evaluations.quotas import (
    MAX_CASES_PER_SUITE,
    MAX_DESIGNER_PROPOSALS_PER_REQUEST,
    MAX_FIXTURE_BYTES,
    QuotaExceeded,
    check_active_executions_per_org,
    check_cases_per_suite,
    check_designer_proposal_count,
    check_evidence_size,
    check_fixture_size,
    check_repetitions,
)


def test_fixture_size_bound():
    assert check_fixture_size({"entities": {}}) > 0
    with pytest.raises(QuotaExceeded):
        check_fixture_size({"blob": "x" * (MAX_FIXTURE_BYTES + 1)})


def test_cases_per_suite_bound():
    check_cases_per_suite(MAX_CASES_PER_SUITE - 1)
    with pytest.raises(QuotaExceeded):
        check_cases_per_suite(MAX_CASES_PER_SUITE)


def test_designer_proposal_count_bound():
    check_designer_proposal_count(MAX_DESIGNER_PROPOSALS_PER_REQUEST)
    with pytest.raises(QuotaExceeded):
        check_designer_proposal_count(0)
    with pytest.raises(QuotaExceeded):
        check_designer_proposal_count(MAX_DESIGNER_PROPOSALS_PER_REQUEST + 1)


def test_active_executions_and_repetitions_bounds():
    check_active_executions_per_org(0)
    with pytest.raises(QuotaExceeded):
        check_active_executions_per_org(5)
    check_repetitions(None)
    check_repetitions(10)
    with pytest.raises(QuotaExceeded):
        check_repetitions(11)


def test_evidence_size_bound():
    assert check_evidence_size({"output": "ok"}) > 0


def test_simulator_record_cap_fails_closed():
    from src.services.agent_evaluations import quotas
    from src.services.agent_evaluations.simulator import (
        Simulator,
        SyntheticToolError,
    )

    fixture = {
        "version": 1,
        "entities": {"ticket": {}},
        "allowed_tools": ["create_ticket"],
        "rules": [
            {
                "tool": "create_ticket",
                "return": {"id": "t-1", "ok": True},
            }
        ],
    }
    sim = Simulator(fixture)
    original = quotas.MAX_SIM_RECORDS_PER_RUN
    quotas.MAX_SIM_RECORDS_PER_RUN = 2
    try:
        sim.call("create_ticket", {"title": "a"})
        sim.call("create_ticket", {"title": "b"})
        with pytest.raises(SyntheticToolError, match="exceeded"):
            sim.call("create_ticket", {"title": "c"})
    finally:
        quotas.MAX_SIM_RECORDS_PER_RUN = original
