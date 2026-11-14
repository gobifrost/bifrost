"""Unit tests for embed redirect URL construction."""

from src.routers.embed import _app_redirect_url


def test_app_redirect_forwards_only_url_encoded_ticket_id_without_mutating_input() -> None:
    query_params = {
        "agent_id": "42",
        "ticket_id": "INC 42&customer=Fabrikam/😀",
        "customer": "Fabrikam",
        "hmac": "signed-value",
    }

    redirect_url = _app_redirect_url("/apps/triage", query_params, "access-token")

    assert redirect_url == (
        "/apps/triage?ticket_id=INC+42%26customer%3DFabrikam%2F%F0%9F%98%80"
        "#embed_token=access-token"
    )
    assert query_params == {
        "agent_id": "42",
        "ticket_id": "INC 42&customer=Fabrikam/😀",
        "customer": "Fabrikam",
        "hmac": "signed-value",
    }


def test_app_redirect_keeps_no_query_shape_for_empty_or_missing_ticket_id() -> None:
    assert _app_redirect_url("/apps/triage", {}, "access-token") == (
        "/apps/triage#embed_token=access-token"
    )
    assert _app_redirect_url(
        "/apps/triage", {"ticket_id": ""}, "access-token"
    ) == "/apps/triage#embed_token=access-token"
