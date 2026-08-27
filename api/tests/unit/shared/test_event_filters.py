from shared.event_filters import evaluate_event_filter


def test_matching_jsonpath_style_comparison() -> None:
    decision = evaluate_event_filter(
        "$.priority == 'high'",
        {"priority": "high"},
    )

    assert decision.matches is True
    assert decision.error is None


def test_non_matching_nested_comparison() -> None:
    decision = evaluate_event_filter(
        "$.ticket.priority == 'high'",
        {"ticket": {"priority": "low"}},
    )

    assert decision.matches is False
    assert decision.error is None


def test_missing_path_fails_closed() -> None:
    decision = evaluate_event_filter(
        "$.missing == 'expected'",
        {"priority": "high"},
    )

    assert decision.matches is False
    assert decision.error is None


def test_invalid_expression_fails_closed_with_error() -> None:
    decision = evaluate_event_filter(
        "$.priority ==",
        {"priority": "high"},
    )

    assert decision.matches is False
    assert decision.error is not None


def test_absent_filter_matches() -> None:
    assert evaluate_event_filter(None, {}).matches is True
    assert evaluate_event_filter("  ", {}).matches is True
