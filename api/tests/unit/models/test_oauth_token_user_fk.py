"""Regression tests for OAuth token ownership semantics."""


def test_oauth_token_user_fk_cascades_on_user_delete():
    """User-owned OAuth tokens must not become org-owned tokens after user delete."""
    from src.models.orm.oauth import OAuthToken

    user_fk = next(
        fk
        for fk in OAuthToken.__table__.foreign_keys
        if fk.parent.name == "user_id"
    )

    assert user_fk.ondelete == "CASCADE"
