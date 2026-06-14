from src.services.events.registry import COMMON_EXAMPLE_FIELDS, CURATED_TOPICS


def test_curated_topics_include_expected_built_ins():
    topics = {entry["topic"] for entry in CURATED_TOPICS}

    assert {
        "user.invited",
        "workflow.failed",
        "workflow.retry_exhausted",
        "integration.connected",
        "integration.disconnected",
        "integration.refresh_failed",
        "integration.reauth_required",
        "integration.refresh_recovered",
        "event.delivery_retry_exhausted",
        "solution.update_available",
    } <= topics


def test_solution_update_available_registered():
    entry = next(
        e for e in CURATED_TOPICS if e["topic"] == "solution.update_available"
    )
    assert entry["description"]
    assert entry["category"] == "Solutions"
    assert entry["example_body"]["solution"]["available_version"] == "1.1.0"


def test_builtin_event_bodies_share_common_envelope_keys():
    for entry in CURATED_TOPICS:
        body = entry["example_body"]

        assert tuple(body.keys())[: len(COMMON_EXAMPLE_FIELDS)] == COMMON_EXAMPLE_FIELDS
        assert body["schema_version"] == 1
        assert set(body["organization"].keys()) == {"id", "name"}
        assert set(body["actor"].keys()) == {"type", "id", "email", "name"}


def test_builtin_event_registry_has_reference_metadata():
    for entry in CURATED_TOPICS:
        assert entry["topic"]
        assert entry["description"]
        assert entry["category"]
        assert entry["emitted_by"]
        assert entry["example_body"]
