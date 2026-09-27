"""Unit tests for the embed-token form-binding check in shared.sdk_forms."""

from types import SimpleNamespace
from uuid import uuid4

from shared.sdk_forms import embed_can_access_form
from src.core.principal import UserPrincipal


FORM_ID = str(uuid4())
OTHER_FORM_ID = str(uuid4())
ORG_ID = uuid4()


def _form(form_id: str = FORM_ID, organization_id=ORG_ID):
    return SimpleNamespace(id=form_id, organization_id=organization_id)


def _principal(**overrides) -> UserPrincipal:
    values = {
        "user_id": uuid4(),
        "email": "embed@example.com",
        "organization_id": ORG_ID,
        "embed": True,
    }
    values.update(overrides)
    return UserPrincipal(**values)


def test_form_token_matching_form_is_allowed():
    principal = _principal(embed_kind="form", form_id=FORM_ID)
    assert embed_can_access_form(principal, _form()) is True


def test_form_token_for_other_form_is_denied():
    principal = _principal(embed_kind="form", form_id=OTHER_FORM_ID)
    assert embed_can_access_form(principal, _form()) is False


def test_app_token_same_org_form_is_denied():
    principal = _principal(embed_kind="app", app_id="app-slug")
    assert embed_can_access_form(principal, _form()) is False


def test_untyped_embed_principal_is_denied():
    principal = _principal()
    assert embed_can_access_form(principal, _form()) is False
