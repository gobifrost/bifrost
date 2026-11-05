"""OAuth SSO separates stable identity parsing from new-link email authority."""

from unittest.mock import MagicMock

import jwt
import pytest

from src.services.oauth_sso import (
    MICROSOFT_CONSUMER_TENANT_ID,
    OAuthError,
    OAuthService,
    OAuthTokens,
)

WORK_TENANT = "6f1d2c3b-0000-4000-8000-00000000abcd"


def _service() -> OAuthService:
    return OAuthService(MagicMock())


def _id_token(tenant_id: str | None) -> str:
    claims = {"oid": "user-oid", **({"tid": tenant_id} if tenant_id else {})}
    return jwt.encode(claims, "signature-is-not-checked-on-sign-in", algorithm="HS256")


def _tokens(tenant_id: str | None = WORK_TENANT) -> OAuthTokens:
    return OAuthTokens(access_token="access-token", id_token=_id_token(tenant_id))


def _graph_me(**overrides) -> dict:
    return {
        "id": "user-oid",
        "displayName": "Kim Braun",
        "userPrincipalName": "Kim.Braun@ClientCo.com",
        "mail": "kim.braun@clientco.com",
        **overrides,
    }


def _google(**overrides) -> dict:
    return {
        "sub": "g-1",
        "email": "kim@clientco.com",
        "email_verified": True,
        "hd": "clientco.com",
        **overrides,
    }


def test_microsoft_identifies_the_user_by_upn_not_the_mail_attribute():
    # Graph mail is free text any tenant admin can set to someone else's address.
    info = _service()._parse_user_info("microsoft", _graph_me(mail="will@kw-corp.com"))

    assert info.email == "kim.braun@clientco.com"
    assert info.provider_user_id == "user-oid"


@pytest.mark.parametrize(
    ("provider", "data"),
    [
        ("microsoft", _graph_me(id=None)),
        ("microsoft", _graph_me(id=42)),
        ("google", _google(sub="   ")),
        ("google", _google(sub=None)),
        ("oidc", {"sub": [], "email": "kim@clientco.com"}),
    ],
)
def test_parse_rejects_missing_or_malformed_stable_provider_ids(provider, data):
    with pytest.raises(OAuthError, match="stable user ID"):
        _service()._parse_user_info(provider, data)


def test_parse_normalizes_claims_without_authorizing_a_new_link():
    info = _service()._parse_user_info(
        "oidc",
        {"sub": "  subject-1  ", "email": " Kim@ClientCo.com ", "name": " Kim ", "email_verified": False},
    )

    assert info.provider_user_id == "  subject-1  "
    assert info.email == "kim@clientco.com"
    assert info.name == " Kim "
    assert info.email_verified is False


@pytest.mark.parametrize(
    ("data", "tokens", "message"),
    [
        (_graph_me(), _tokens(MICROSOFT_CONSUMER_TENANT_ID), "Personal Microsoft accounts"),
        (_graph_me(userPrincipalName="kim_gmail.com#EXT#@clientco.onmicrosoft.com"), _tokens(), "Guest accounts"),
        (_graph_me(), OAuthTokens(access_token="access-token"), "didn't return an ID token"),
        (_graph_me(), OAuthTokens(access_token="access-token", id_token="not-a-jwt"), "unreadable ID token"),
        (_graph_me(), _tokens(None), "ID token has no tenant"),
    ],
)
def test_microsoft_rejects_untrusted_new_links(data, tokens, message):
    service = _service()
    with pytest.raises(OAuthError, match=message):
        service.validate_email_for_linking(service._parse_user_info("microsoft", data), tokens)


def test_google_workspace_account_can_link_an_email_from_a_different_domain():
    service = _service()
    info = service._parse_user_info("google", _google(hd="workspace.example"))

    service.validate_email_for_linking(info, _tokens())


def test_google_personal_account_on_a_work_address_cannot_link():
    service = _service()
    with pytest.raises(OAuthError, match="clientco.com Google Workspace account"):
        service.validate_email_for_linking(service._parse_user_info("google", _google(hd=None)), _tokens())


def test_gmail_address_can_link_without_a_workspace_domain():
    service = _service()
    info = service._parse_user_info("google", _google(email="kim@gmail.com", hd=None))

    service.validate_email_for_linking(info, _tokens())


@pytest.mark.parametrize("verified", [False, None])
def test_google_requires_an_explicitly_verified_email_for_a_new_link(verified):
    service = _service()
    with pytest.raises(OAuthError, match="verified"):
        service.validate_email_for_linking(service._parse_user_info("google", _google(email_verified=verified)), _tokens())


def test_oidc_trusts_a_missing_verification_claim_but_refuses_explicit_false():
    service = _service()
    service.validate_email_for_linking(
        service._parse_user_info("oidc", {"sub": "o-1", "email": "Kim@ClientCo.com"}), _tokens()
    )

    with pytest.raises(OAuthError, match="verified"):
        service.validate_email_for_linking(
            service._parse_user_info(
                "oidc", {"sub": "o-1", "email": "kim@clientco.com", "email_verified": False}
            ),
            _tokens(),
        )
