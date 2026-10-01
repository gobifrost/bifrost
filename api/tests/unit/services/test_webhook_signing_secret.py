"""The webhook signing secret is write-only and stored only as ciphertext."""

from types import SimpleNamespace

import pytest

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.adapters.microsoft_graph import MicrosoftGraphAdapter
from src.services.webhooks.signing_secret import (
    STATE_KEY,
    carry_signing_secret,
    generate_signing_secret,
    read_signing_secret,
    signing_secret_set,
    split_signing_secret,
    uses_signing_secret,
    with_signing_secret,
)


def test_split_removes_the_secret_from_config():
    config, sent, secret = split_signing_secret(
        {"secret": "s3cret", "signature_header": "X-Hub-Signature-256"}
    )

    assert config == {"signature_header": "X-Hub-Signature-256"}
    assert sent is True
    assert secret == "s3cret"


def test_split_distinguishes_absent_from_cleared():
    assert split_signing_secret({"event_type_field": "type"}) == (
        {"event_type_field": "type"},
        False,
        None,
    )
    assert split_signing_secret({"secret": None}) == ({}, True, None)
    assert split_signing_secret({"secret": ""}) == ({}, True, None)


def test_split_rejects_a_non_string_secret():
    with pytest.raises(ValueError, match="must be a string"):
        split_signing_secret({"secret": 12345})


def test_stored_secret_is_ciphertext_and_decrypts():
    state = with_signing_secret({"other": 1}, "s3cret")

    assert state["other"] == 1
    assert "s3cret" not in str(state)
    assert signing_secret_set(state) is True
    assert read_signing_secret(state) == "s3cret"


def test_storing_none_clears_the_secret():
    state = with_signing_secret(with_signing_secret({}, "s3cret"), None)

    assert STATE_KEY not in state
    assert signing_secret_set(state) is False
    assert read_signing_secret(state) is None


def test_carry_keeps_the_previous_secret_in_new_provider_state():
    previous = with_signing_secret({"old": True}, "s3cret")

    carried = carry_signing_secret(previous, {"new": True})

    assert carried == {"new": True, STATE_KEY: previous[STATE_KEY]}
    assert carry_signing_secret({"old": True}, {"new": True}) == {"new": True}


def test_only_adapters_declaring_a_secret_use_one():
    assert uses_signing_secret(GenericWebhookAdapter()) is True
    assert uses_signing_secret(MicrosoftGraphAdapter()) is False
    assert uses_signing_secret(SimpleNamespace(config_schema={})) is False


def test_generated_secrets_are_distinct_and_long():
    first, second = generate_signing_secret(), generate_signing_secret()

    assert first != second
    assert len(first) >= 32
