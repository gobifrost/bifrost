from types import SimpleNamespace

import pytest

from shared.form_runtime import (
    FormRuntimeValidationError,
    form_capability_fingerprint,
    normalize_allowed_origins,
    validate_form_submission,
)


def _field(**overrides):
    values = {
        "position": 0,
        "name": "customer",
        "type": "select",
        "data_provider_id": None,
        "data_provider_inputs": None,
        "auto_fill": None,
        "allowed_types": None,
        "multiple": None,
        "max_size_mb": None,
        "required": False,
        "default_value": None,
        "options": None,
        "validation": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _form(**overrides):
    values = {
        "workflow_id": "workflow-1",
        "launch_workflow_id": None,
        "name": "Original name",
        "description": "Original description",
        "confirmation_markdown": "Thanks",
        "fields": [_field()],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_capability_fingerprint_ignores_cosmetic_content():
    original = form_capability_fingerprint(_form())

    changed = form_capability_fingerprint(
        _form(
            name="Renamed",
            description="Changed",
            confirmation_markdown="Different confirmation",
        )
    )

    assert changed == original


def test_capability_fingerprint_changes_for_provider_and_file_powers():
    original = form_capability_fingerprint(_form())
    changed = form_capability_fingerprint(
        _form(
            fields=[
                _field(
                    data_provider_id="provider-1",
                    data_provider_inputs={"country": {"mode": "static", "value": "US"}},
                    auto_fill={"email": "contact.email"},
                ),
                _field(
                    position=1,
                    name="attachment",
                    type="file",
                    allowed_types=["image/png"],
                    max_size_mb=5,
                ),
            ]
        )
    )

    assert changed != original


def test_normalize_allowed_origins_canonicalizes_and_deduplicates():
    assert normalize_allowed_origins(
        ["https://EXAMPLE.com:443", "https://example.com", "http://localhost:3000"]
    ) == ["http://localhost:3000", "https://example.com"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://*.example.com",
        "https://example.com/path",
        "https://user@example.com",
        "javascript://example.com",
        "https://example.com\nframe-ancestors *",
        " https://example.com",
    ],
)
def test_normalize_allowed_origins_rejects_non_origins(origin):
    with pytest.raises(ValueError):
        normalize_allowed_origins([origin])


def test_submission_validation_rejects_unknown_display_and_missing_fields():
    form = _form(
        fields=[
            _field(name="email", type="email", required=True),
            _field(position=1, name="intro", type="markdown"),
        ]
    )

    with pytest.raises(FormRuntimeValidationError) as exc_info:
        validate_form_submission(form, {"intro": "forged", "extra": "value"})

    assert exc_info.value.errors == [
        {"field": "extra", "message": "Unknown form field"},
        {"field": "intro", "message": "Unknown form field"},
        {"field": "email", "message": "This field is required"},
    ]


def test_submission_validation_enforces_types_options_and_rules():
    form = _form(
        fields=[
            _field(
                name="count",
                type="number",
                validation={"min": 2, "max": 4},
            ),
            _field(
                position=1,
                name="choice",
                type="select",
                options=[{"label": "A", "value": "a"}],
            ),
            _field(
                position=2,
                name="code",
                type="text",
                validation={"pattern": "[A-Z]{3}"},
            ),
        ]
    )

    assert validate_form_submission(
        form, {"count": 3, "choice": "a", "code": "ABC"}
    ) == {"count": 3, "choice": "a", "code": "ABC"}

    with pytest.raises(FormRuntimeValidationError) as exc_info:
        validate_form_submission(
            form, {"count": True, "choice": "b", "code": "abc"}
        )
    assert {error["field"] for error in exc_info.value.errors} == {
        "count",
        "choice",
        "code",
    }


def test_submission_validation_enforces_email_and_iso_dates():
    form = _form(
        fields=[
            _field(name="email", type="email"),
            _field(position=1, name="day", type="date"),
            _field(position=2, name="starts_at", type="datetime"),
        ]
    )

    assert validate_form_submission(
        form,
        {
            "email": "visitor@example.com",
            "day": "2026-08-04",
            "starts_at": "2026-08-04T12:30:00Z",
        },
    )["email"] == "visitor@example.com"

    with pytest.raises(FormRuntimeValidationError) as exc_info:
        validate_form_submission(
            form,
            {"email": "invalid", "day": "tomorrow", "starts_at": "later"},
        )
    assert {error["field"] for error in exc_info.value.errors} == {
        "email",
        "day",
        "starts_at",
    }


def test_embed_file_validation_requires_session_owned_paths_and_count_shape():
    form = _form(
        fields=[_field(name="attachment", type="file", multiple=False)]
    )
    prefix = "form-1/session-1/"

    assert validate_form_submission(
        form,
        {"attachment": f"{prefix}upload-1/report.pdf"},
        embed_upload_prefix=prefix,
    ) == {"attachment": f"{prefix}upload-1/report.pdf"}

    for forged in (
        "form-1/session-2/upload-1/report.pdf",
        "form-1/session-1/../session-2/report.pdf",
        [
            "form-1/session-1/upload-1/a.pdf",
            "form-1/session-1/upload-2/b.pdf",
        ],
    ):
        with pytest.raises(FormRuntimeValidationError):
            validate_form_submission(
                form,
                {"attachment": forged},
                embed_upload_prefix=prefix,
            )
