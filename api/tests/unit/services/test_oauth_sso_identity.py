"""SSO sign-in only trusts an email address the identity provider has proven the user owns.

Sign-in links to existing Bifrost users, and auto-provisions new ones, by email, so an
address the user (or their tenant admin) can simply type in would let them take over
another person's account.
"""

from unittest.mock import MagicMock

import jwt
import pytest

from src.services.oauth_sso import MICROSOFT_CONSUMER_TENANT_ID, OAuthError, OAuthService

WORK_TENANT = "6f1d2c3b-0000-4000-8000-00000000abcd"


def _service() -> OAuthService:
    return OAuthService(MagicMock())


def _id_token(tenant_id: str | None) -> str:
    claims = {"oid": "user-oid", **({"tid": tenant_id} if tenant_id else {})}
    return jwt.encode(claims, "signature-is-not-checked-on-sign-in", algorithm="HS256")


def _graph_me(**overrides) -> dict:
    return {
        "id": "user-oid",
        "displayName": "Kim Braun",
        "userPrincipalName": "Kim.Braun@ClientCo.com",
        "mail": "kim.braun@clientco.com",
        **overrides,
    }


def test_microsoft_identifies_the_user_by_upn_not_the_mail_attribute():
    # Any tenant admin can set `mail` to someone else's address; the UPN's domain is verified.
    info = _service()._parse_user_info(
        "microsoft", _graph_me(mail="will@kw-corp.com"), _id_token(WORK_TENANT)
    )
    assert info.email == "kim.braun@clientco.com"
    assert info.provider_user_id == "user-oid"


def test_microsoft_refuses_personal_accounts():
    with pytest.raises(OAuthError, match="Personal Microsoft accounts"):
        _service()._parse_user_info("microsoft", _graph_me(), _id_token(MICROSOFT_CONSUMER_TENANT_ID))


def test_microsoft_refuses_guest_accounts():
    guest = _graph_me(userPrincipalName="kim_gmail.com#EXT#@clientco.onmicrosoft.com")
    with pytest.raises(OAuthError, match="Guest accounts"):
        _service()._parse_user_info("microsoft", guest, _id_token(WORK_TENANT))


@pytest.mark.parametrize("id_token", [None, "not-a-jwt", _id_token(None)])
def test_microsoft_refuses_a_sign_in_without_a_readable_tenant(id_token):
    with pytest.raises(OAuthError):
        _service()._parse_user_info("microsoft", _graph_me(), id_token)


def _google(**overrides) -> dict:
    return {"sub": "g-1", "email": "kim@clientco.com", "email_verified": True, "hd": "clientco.com", **overrides}


def test_google_workspace_account_signs_in_with_its_domain_address():
    info = _service()._parse_user_info("google", _google())
    assert info.email == "kim@clientco.com"


def test_google_personal_account_on_a_work_address_is_refused():
    # A consumer Google account can be registered on any address and has no `hd`.
    with pytest.raises(OAuthError, match="clientco.com Google Workspace account"):
        _service()._parse_user_info("google", _google(hd=None))


def test_google_workspace_account_cannot_claim_another_domain():
    with pytest.raises(OAuthError, match="Google Workspace account"):
        _service()._parse_user_info("google", _google(hd="othercorp.com"))


def test_gmail_address_signs_in_without_a_workspace_domain():
    info = _service()._parse_user_info("google", _google(email="kim@gmail.com", hd=None))
    assert info.email == "kim@gmail.com"


@pytest.mark.parametrize("verified", [False, None])
def test_google_unverified_email_is_refused(verified):
    with pytest.raises(OAuthError, match="verified"):
        _service()._parse_user_info("google", _google(email_verified=verified))


def test_oidc_provider_is_trusted_unless_it_says_the_email_is_unverified():
    service = _service()
    assert service._parse_user_info("oidc", {"sub": "o-1", "email": "Kim@ClientCo.com"}).email == "kim@clientco.com"
    with pytest.raises(OAuthError, match="verified"):
        service._parse_user_info("oidc", {"sub": "o-1", "email": "kim@clientco.com", "email_verified": False})
