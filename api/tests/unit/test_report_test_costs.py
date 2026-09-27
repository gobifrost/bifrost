"""The timing report groups parametrized and class-based tests by source file."""

import sys

from scripts.report_test_costs import main, summarize


def test_summarize_junit_by_module(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuite>'
        '<testcase classname="tests.e2e.api.test_apps.TestApps" name="test_one[a]" time="2.5" />'
        '<testcase classname="tests.e2e.api.test_apps.TestApps" name="test_one[b]" time="3.5" />'
        '<testcase classname="tests.e2e.api.test_jobs" name="test_job" time="10" />'
        '</testsuite>',
        encoding="utf-8",
    )

    cases, modules = summarize(report)

    assert [case[1] for case in cases] == [10.0, 3.5, 2.5]
    assert modules == [
        ("tests.e2e.api.test_jobs", 10.0, 1),
        ("tests.e2e.api.test_apps", 6.0, 2),
    ]


def test_cli_reports_two_second_review_bucket(tmp_path, monkeypatch, capsys):
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuite>'
        '<testcase classname="tests.e2e.api.test_apps" name="test_fast" time="1" />'
        '<testcase classname="tests.e2e.api.test_apps" name="test_review" time="2.5" />'
        '</testsuite>',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["report_test_costs.py", str(report)])

    main()

    assert "Cases >= 2s: 1; summed time: 2.5s" in capsys.readouterr().out
