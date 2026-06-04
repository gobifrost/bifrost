"""E2E tests for user invite endpoints (#227).

Verifies the invite-management endpoints on the users router and the
unauthenticated register-from-invite flow on the auth router.
"""

from uuid import UUID

import pytest

from src.models.orm import UserOAuthAccount


@pytest.mark.e2e
class TestUserInviteFlags:
    """invite_status field on UserPublic."""

    def test_create_user_creates_registration_invite(
        self, e2e_client, platform_admin, org1
    ):
        """Admin-created users get a pending registration link by default."""
        resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-noinv@gobifrost.dev",
                "name": "No Invite",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["invite_status"] == "pending"
        assert "accept-invite?token=" in body["registration_url"]
        # Cleanup
        e2e_client.patch(
            f"/api/users/{body['id']}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{body['id']}", headers=platform_admin.headers
        )

    def test_listing_includes_invite_status(self, e2e_client, platform_admin):
        """List response includes invite_status on every user."""
        resp = e2e_client.get(
            "/api/users", headers=platform_admin.headers
        )
        assert resp.status_code == 200
        for u in resp.json():
            assert "invite_status" in u
            assert u["invite_status"] in {"active", "pending", "expired", "never_invited"}

    async def test_oauth_linked_user_invite_status_is_active(
        self, e2e_client, platform_admin, org1, db_session
    ):
        """Historical SSO users are active even if is_registered was never backfilled."""
        email = "inv-oauth-linked@gobifrost.dev"
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": email,
                "name": "OAuth Linked",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201
        body = create_resp.json()
        user_id = UUID(body["id"])

        db_session.add(
            UserOAuthAccount(
                user_id=user_id,
                provider_id="oidc",
                provider_user_id="historical-sso-user",
                email=email,
            )
        )
        await db_session.commit()

        list_resp = e2e_client.get(
            "/api/users", headers=platform_admin.headers
        )
        target = next(u for u in list_resp.json() if u["id"] == body["id"])
        assert target["is_registered"] is False
        assert target["invite_status"] == "active"

        e2e_client.patch(
            f"/api/users/{body['id']}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{body['id']}", headers=platform_admin.headers
        )


@pytest.mark.e2e
class TestInviteEndpoints:
    """Endpoints under /api/users/{id}/invite."""

    def test_regenerate_returns_registration_url(
        self, e2e_client, platform_admin, org1
    ):
        """POST /invite/regenerate returns a token-bearing URL and does not send email."""
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-regen@gobifrost.dev",
                "name": "Regen",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        regen_resp = e2e_client.post(
            f"/api/users/{user_id}/invite/regenerate",
            headers=platform_admin.headers,
        )
        assert regen_resp.status_code == 200
        body = regen_resp.json()
        assert "accept-invite?token=" in body["registration_url"]
        assert body["event_emitted"] is False

        # User should now show invite_status=pending
        list_resp = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
        )
        target = next(u for u in list_resp.json() if u["id"] == user_id)
        assert target["invite_status"] == "pending"

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{user_id}", headers=platform_admin.headers
        )

    def test_revoke_invite_returns_to_never_invited(
        self, e2e_client, platform_admin, org1
    ):
        """DELETE /invite revokes the invite; status returns to never_invited."""
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-rev@gobifrost.dev",
                "name": "Revoke",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        user_id = create_resp.json()["id"]

        e2e_client.post(
            f"/api/users/{user_id}/invite/regenerate",
            headers=platform_admin.headers,
        )
        revoke_resp = e2e_client.delete(
            f"/api/users/{user_id}/invite",
            headers=platform_admin.headers,
        )
        assert revoke_resp.status_code == 204

        list_resp = e2e_client.get(
            "/api/users", headers=platform_admin.headers
        )
        target = next(u for u in list_resp.json() if u["id"] == user_id)
        assert target["invite_status"] == "never_invited"

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{user_id}", headers=platform_admin.headers
        )

    def test_regenerate_for_registered_user_409(
        self, e2e_client, platform_admin
    ):
        """Cannot generate invite for already-registered user."""
        # platform_admin is already registered.
        resp = e2e_client.post(
            f"/api/users/{platform_admin.user_id}/invite/regenerate",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 409


@pytest.mark.e2e
class TestRegisterFromInvite:
    """POST /auth/register-from-invite consumes a single-use token."""

    def test_register_consumes_token_and_marks_user_registered(
        self, e2e_client, platform_admin, org1
    ):
        """Happy path: regenerate invite, then redeem the token."""
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-reg@gobifrost.dev",
                "name": "RegFromInvite",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        user_id = create_resp.json()["id"]

        regen = e2e_client.post(
            f"/api/users/{user_id}/invite/regenerate",
            headers=platform_admin.headers,
        )
        url: str = regen.json()["registration_url"]
        token = url.split("token=", 1)[1]

        register_resp = e2e_client.post(
            "/auth/register-from-invite",
            json={"token": token, "password": "supersecret-1234"},
        )
        assert register_resp.status_code == 200
        assert register_resp.json()["email"] == "inv-reg@gobifrost.dev"
        assert register_resp.json()["is_registered"] is True

        # Replay must fail
        replay = e2e_client.post(
            "/auth/register-from-invite",
            json={"token": token, "password": "x"},
        )
        assert replay.status_code == 400

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{user_id}", headers=platform_admin.headers
        )

    def test_register_without_password_creates_passwordless_user(
        self, e2e_client, platform_admin, org1
    ):
        """Invite registration may complete without enabling password auth."""
        email = "inv-passkey@gobifrost.dev"
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": email,
                "name": "Passkey Invite",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        user_id = create_resp.json()["id"]

        regen = e2e_client.post(
            f"/api/users/{user_id}/invite/regenerate",
            headers=platform_admin.headers,
        )
        url: str = regen.json()["registration_url"]
        token = url.split("token=", 1)[1]

        register_resp = e2e_client.post(
            "/auth/register-from-invite",
            json={"token": token},
        )
        assert register_resp.status_code == 200
        assert register_resp.json()["email"] == email
        assert register_resp.json()["is_registered"] is True

        login_resp = e2e_client.post(
            "/auth/login",
            data={"username": email, "password": "anything"},
        )
        assert login_resp.status_code == 401
        assert (
            login_resp.json()["detail"]
            == "Account does not have password authentication enabled"
        )

        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{user_id}", headers=platform_admin.headers
        )

    def test_register_unknown_token_400(self, e2e_client):
        """Unknown token returns 400, not 200/500."""
        resp = e2e_client.post(
            "/auth/register-from-invite",
            json={"token": "garbage-token", "password": "x"},
        )
        assert resp.status_code == 400


@pytest.mark.e2e
class TestUserInvitedEvent:
    """user.invited event is emitted at the right times."""

    def test_send_invite_emits_event_for_existing_link(
        self, e2e_client, platform_admin, org1
    ):
        """POST /invite/send emits user.invited without rotating the token."""
        resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-event-create@gobifrost.dev",
                "name": "Event Create",
                "organization_id": org1["id"],
                "is_superuser": False,
                "invite": True,
            },
        )
        assert resp.status_code == 201
        create_body = resp.json()
        user_id = create_body["id"]
        assert create_body["invite_status"] == "pending"
        assert "accept-invite?token=" in create_body["registration_url"]

        send_resp = e2e_client.post(
            f"/api/users/{user_id}/invite/send",
            headers=platform_admin.headers,
            json={"registration_url": create_body["registration_url"]},
        )
        assert send_resp.status_code == 200
        body = send_resp.json()
        assert body["event_emitted"] is True
        assert body["event_id"] is not None
        assert body["registration_url"] == create_body["registration_url"]

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)

    def test_send_invite_rejects_invalid_registration_url(
        self, e2e_client, platform_admin, org1
    ):
        """POST /invite/send requires a token-bearing registration URL."""
        resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-event-invalid-url@gobifrost.dev",
                "name": "Event Invalid URL",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert resp.status_code == 201
        user_id = resp.json()["id"]

        send_resp = e2e_client.post(
            f"/api/users/{user_id}/invite/send",
            headers=platform_admin.headers,
            json={"registration_url": "https://example.test/accept-invite"},
        )
        assert send_resp.status_code == 400

        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)

    def test_send_invite_rejects_link_for_another_user(
        self, e2e_client, platform_admin, org1
    ):
        """POST /invite/send cannot send a token belonging to a different user."""
        first = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-event-owner-a@gobifrost.dev",
                "name": "Owner A",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        second = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-event-owner-b@gobifrost.dev",
                "name": "Owner B",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert first.status_code == 201
        assert second.status_code == 201
        first_body = first.json()
        second_body = second.json()

        send_resp = e2e_client.post(
            f"/api/users/{second_body['id']}/invite/send",
            headers=platform_admin.headers,
            json={"registration_url": first_body["registration_url"]},
        )
        assert send_resp.status_code == 400

        for body in (first_body, second_body):
            e2e_client.patch(
                f"/api/users/{body['id']}",
                headers=platform_admin.headers,
                json={"is_active": False},
            )
            e2e_client.delete(
                f"/api/users/{body['id']}", headers=platform_admin.headers
            )

    def test_resend_invite_emits_event(self, e2e_client, platform_admin, org1):
        """POST /users/{id}/invite/resend returns event_emitted=True."""
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-event-resend@gobifrost.dev",
                "name": "Event Resend",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        user_id = create_resp.json()["id"]

        resend_resp = e2e_client.post(
            f"/api/users/{user_id}/invite/resend",
            headers=platform_admin.headers,
        )
        assert resend_resp.status_code == 200
        body = resend_resp.json()
        assert body["event_emitted"] is True
        assert body["event_id"] is not None

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)

    def test_regenerate_does_not_emit_event(self, e2e_client, platform_admin, org1):
        """POST /users/{id}/invite/regenerate returns event_emitted=False."""
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-event-regen@gobifrost.dev",
                "name": "Event Regen",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        user_id = create_resp.json()["id"]

        regen_resp = e2e_client.post(
            f"/api/users/{user_id}/invite/regenerate",
            headers=platform_admin.headers,
        )
        assert regen_resp.status_code == 200
        body = regen_resp.json()
        assert body["event_emitted"] is False
        assert body["event_id"] is None

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)

    def test_create_with_trigger_automation_false_still_creates_link(
        self, e2e_client, platform_admin, org1
    ):
        """Legacy automation flags do not stop invite-link creation."""
        resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-no-auto@gobifrost.dev",
                "name": "No Auto",
                "organization_id": org1["id"],
                "is_superuser": False,
                "invite": True,
                "trigger_automation": False,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["invite_status"] == "pending"
        assert "accept-invite?token=" in body["registration_url"]
        user_id = body["id"]

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)

    def test_registration_url_in_event_payload_contains_token(
        self, e2e_client, platform_admin, org1
    ):
        """Resend returns a registration_url with the token embedded."""
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inv-url-check@gobifrost.dev",
                "name": "URL Check",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        user_id = create_resp.json()["id"]

        resend_resp = e2e_client.post(
            f"/api/users/{user_id}/invite/resend",
            headers=platform_admin.headers,
        )
        body = resend_resp.json()
        assert "accept-invite?token=" in body["registration_url"]

        # Cleanup
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestInvitePermissions:
    """Invite-management endpoints require platform admin auth."""

    def test_regenerate_requires_superuser(
        self, e2e_client, org1_user, platform_admin
    ):
        # need a non-self user_id to attempt regen on
        target_id = platform_admin.user_id
        resp = e2e_client.post(
            f"/api/users/{target_id}/invite/regenerate",
            headers=org1_user.headers,
        )
        # CurrentSuperuser dependency should reject non-superuser
        assert resp.status_code in (401, 403)

    def test_revoke_requires_superuser(self, e2e_client, org1_user, platform_admin):
        target_id = platform_admin.user_id
        resp = e2e_client.delete(
            f"/api/users/{target_id}/invite",
            headers=org1_user.headers,
        )
        assert resp.status_code in (401, 403)
