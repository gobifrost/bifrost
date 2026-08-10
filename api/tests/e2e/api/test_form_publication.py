"""E2E coverage for public form publication approval and lifecycle."""

import uuid
from uuid import UUID

import altcha
import pytest

from src.core.security import decode_token
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.conftest import poll_until, write_and_register
from tests.e2e.platform.conftest import deploy_solution


def _solve_public_captcha(e2e_client, form_id: str, headers: dict[str, str]) -> str:
    response = e2e_client.post(
        f"/api/forms/{form_id}/captcha/challenge", headers=headers
    )
    assert response.status_code == 200, response.text
    challenge = altcha.Challenge.from_dict(response.json())
    solution = altcha.solve_challenge(challenge)
    assert solution is not None
    return altcha.Payload(challenge, solution).to_base64()


@pytest.mark.e2e
class TestFormPublication:
    @pytest.fixture
    def publishable_form(self, e2e_client, platform_admin):
        workflow_content = '''from bifrost import workflow

@workflow(name="e2e_public_form_submit")
async def e2e_public_form_submit(
    email: str | None = None,
    attachment: str | None = None,
):
    return {"received": bool(email), "attachment": bool(attachment)}
'''
        workflow = write_and_register(
            e2e_client,
            platform_admin.headers,
            "e2e_public_form_submit.py",
            workflow_content,
            "e2e_public_form_submit",
        )
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Public form lifecycle",
                "workflow_id": workflow["id"],
                "form_schema": {
                    "fields": [
                        {
                            "name": "email",
                            "label": "Email",
                            "type": "email",
                            "required": True,
                        }
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, response.text
        form = response.json()

        yield form

        e2e_client.delete(
            f"/api/forms/{form['id']}", headers=platform_admin.headers
        )
        e2e_client.delete(
            "/api/files/editor?path=e2e_public_form_submit.py",
            headers=platform_admin.headers,
        )

    def test_publish_change_review_rotate_and_unpublish(
        self, e2e_client, platform_admin, publishable_form
    ):
        form_id = publishable_form["id"]
        review_response = e2e_client.get(
            f"/api/forms/{form_id}/publication-review",
            headers=platform_admin.headers,
        )
        assert review_response.status_code == 200, review_response.text
        review = review_response.json()
        assert review["blockers"] == []
        assert review["submission_workflow"]["name"] == "e2e_public_form_submit"

        unpublished = e2e_client.get(
            f"/api/forms/{form_id}/publication", headers=platform_admin.headers
        )
        assert unpublished.status_code == 200, unpublished.text
        assert unpublished.json()["status"] == "unpublished"

        publish = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={
                "reviewed_fingerprint": review["fingerprint"],
                "allowed_origins": [
                    "https://EXAMPLE.com:443",
                    "http://localhost:3000",
                ],
            },
        )
        assert publish.status_code == 200, publish.text
        published = publish.json()
        assert published["status"] == "published"
        assert published["spam_protection_enabled"] is True
        assert published["allowed_origins"] == [
            "http://localhost:3000",
            "https://example.com",
        ]
        assert published["iframe_path"].endswith(published["public_key"])
        first_key = published["public_key"]

        protection_update = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={
                "reviewed_fingerprint": review["fingerprint"],
                "allowed_origins": published["allowed_origins"],
                "spam_protection_enabled": False,
            },
        )
        assert protection_update.status_code == 200, protection_update.text
        assert protection_update.json()["spam_protection_enabled"] is False

        frame_policy = e2e_client.get(
            f"/embed/forms/public/{first_key}/frame-policy"
        )
        assert frame_policy.status_code == 204, frame_policy.text
        assert frame_policy.headers["content-security-policy"] == (
            "frame-ancestors http://localhost:3000 https://example.com"
        )

        update = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={
                "form_schema": {
                    "fields": [
                        {
                            "name": "email",
                            "label": "Email",
                            "type": "email",
                            "required": True,
                        },
                        {
                            "name": "attachment",
                            "label": "Attachment",
                            "type": "file",
                            "allowed_types": ["image/png"],
                            "max_size_mb": 5,
                        },
                    ]
                }
            },
        )
        assert update.status_code == 200, update.text

        needs_review = e2e_client.get(
            f"/api/forms/{form_id}/publication", headers=platform_admin.headers
        )
        assert needs_review.status_code == 200, needs_review.text
        assert needs_review.json()["status"] == "needs_review"

        stale_publish = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={
                "reviewed_fingerprint": review["fingerprint"],
                "allowed_origins": [],
            },
        )
        assert stale_publish.status_code == 409, stale_publish.text

        changed_review = e2e_client.get(
            f"/api/forms/{form_id}/publication-review",
            headers=platform_admin.headers,
        ).json()
        republish = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={
                "reviewed_fingerprint": changed_review["fingerprint"],
                "allowed_origins": [],
            },
        )
        assert republish.status_code == 200, republish.text
        assert republish.json()["status"] == "published"

        rotate = e2e_client.post(
            f"/api/forms/{form_id}/publication/rotate-key",
            headers=platform_admin.headers,
        )
        assert rotate.status_code == 200, rotate.text
        assert rotate.json()["public_key"] != first_key

        unpublish = e2e_client.delete(
            f"/api/forms/{form_id}/publication", headers=platform_admin.headers
        )
        assert unpublish.status_code == 204, unpublish.text
        final_state = e2e_client.get(
            f"/api/forms/{form_id}/publication", headers=platform_admin.headers
        )
        assert final_state.status_code == 200, final_state.text
        assert final_state.json()["status"] == "unpublished"
        assert final_state.json()["iframe_path"] is None

    def test_first_publication_of_solution_managed_form(
        self, e2e_client, platform_admin
    ):
        headers = platform_admin.headers
        suffix = uuid.uuid4().hex[:8]
        solution = e2e_client.post(
            "/api/solutions",
            headers=headers,
            json={
                "slug": f"public-form-{suffix}",
                "name": f"Public Form {suffix}",
                "organization_id": None,
            },
        )
        assert solution.status_code in (200, 201), solution.text
        solution_id = solution.json()["id"]

        workflow_id = uuid.uuid4()
        form_id = uuid.uuid4()
        deployed = deploy_solution(
            e2e_client,
            solution_id,
            headers,
            {
                "python_files": {
                    "workflows/public_form.py": (
                        "from bifrost import workflow\n\n"
                        "@workflow\n"
                        "async def public_form(email: str | None = None):\n"
                        "    return {'received': bool(email)}\n"
                    )
                },
                "workflows": [
                    {
                        "id": str(workflow_id),
                        "name": f"public_form_{suffix}",
                        "function_name": "public_form",
                        "path": "workflows/public_form.py",
                        "type": "workflow",
                    }
                ],
                "forms": [
                    {
                        "id": str(form_id),
                        "name": f"Public Form {suffix}",
                        "workflow_id": str(workflow_id),
                        "access_level": "authenticated",
                        "form_schema": {
                            "fields": [
                                {
                                    "name": "email",
                                    "label": "Email",
                                    "type": "email",
                                    "required": True,
                                }
                            ]
                        },
                    }
                ],
            },
        )
        assert deployed.status_code in (200, 201), deployed.text

        managed_form_id = solution_entity_id(UUID(solution_id), form_id)
        review_response = e2e_client.get(
            f"/api/forms/{managed_form_id}/publication-review",
            headers=headers,
        )
        assert review_response.status_code == 200, review_response.text
        review = review_response.json()
        assert review["blockers"] == []

        published = e2e_client.put(
            f"/api/forms/{managed_form_id}/publication",
            headers=headers,
            json={
                "reviewed_fingerprint": review["fingerprint"],
                "allowed_origins": ["https://example.com"],
            },
        )
        assert published.status_code == 200, published.text
        body = published.json()
        assert body["status"] == "published"
        assert body["iframe_path"].endswith(body["public_key"])

    def test_public_session_is_exact_form_confirmation_only_and_revocable(
        self, e2e_client, platform_admin, publishable_form
    ):
        form_id = publishable_form["id"]
        review = e2e_client.get(
            f"/api/forms/{form_id}/publication-review",
            headers=platform_admin.headers,
        ).json()
        publication = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={"reviewed_fingerprint": review["fingerprint"], "allowed_origins": []},
        ).json()

        bootstrap = e2e_client.get(
            f"/embed/forms/public/{publication['public_key']}",
            follow_redirects=False,
        )
        assert bootstrap.status_code == 302, bootstrap.text
        location = bootstrap.headers["location"]
        assert location.startswith(
            f"/embedded/forms/public/{publication['public_key']}#embed_token="
        )
        token = location.split("#embed_token=")[1]
        claims = decode_token(token, expected_type="access")
        assert claims is not None
        assert claims["embed_kind"] == "form"
        assert claims["grant"] == "public"
        assert claims["form_id"] == form_id
        assert claims["name"] == "Public Form · Public form lifecycle"
        assert claims["capability_fingerprint"] == review["fingerprint"]
        headers = {"Authorization": f"Bearer {token}"}

        appearance_bootstrap = e2e_client.get(
            f"/embed/forms/public/{publication['public_key']}",
            params={
                "theme": "dark",
                "header": "false",
                "background": "transparent",
            },
            follow_redirects=False,
        )
        assert appearance_bootstrap.headers["location"].startswith(
            f"/embedded/forms/public/{publication['public_key']}"
            "?theme=dark&header=false&background=transparent#embed_token="
        )

        runtime = e2e_client.get(
            f"/api/forms/{form_id}/runtime", headers=headers
        )
        assert runtime.status_code == 200, runtime.text
        assert runtime.json()["captcha_required"] is True
        assert e2e_client.get(
            f"/api/forms/{form_id}", headers=headers
        ).status_code == 403
        assert e2e_client.post(
            "/api/workflows/execute", headers=headers, json={}
        ).status_code == 403

        cosmetic_update = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={"confirmation_markdown": "## All set\n\nWe received it."},
        )
        assert cosmetic_update.status_code == 200, cosmetic_update.text
        assert e2e_client.get(
            f"/api/forms/{form_id}/runtime", headers=headers
        ).status_code == 200

        invalid = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=headers,
            json={
                "form_data": {"unknown": "value"},
                "submission_nonce": "nonce-000000000001",
            },
        )
        assert invalid.status_code == 422, invalid.text

        missing_captcha = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=headers,
            json={
                "form_data": {"email": "visitor@example.com"},
                "submission_nonce": "nonce-000000000001",
            },
        )
        assert missing_captcha.status_code == 422, missing_captcha.text
        assert missing_captcha.json()["detail"] == "Verification is required"

        captcha_payload = _solve_public_captcha(e2e_client, form_id, headers)
        other_bootstrap = e2e_client.get(
            f"/embed/forms/public/{publication['public_key']}",
            follow_redirects=False,
        )
        other_token = other_bootstrap.headers["location"].split("#embed_token=")[1]
        cross_session = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers={"Authorization": f"Bearer {other_token}"},
            json={
                "form_data": {"email": "visitor@example.com"},
                "submission_nonce": "nonce-000000000099",
                "captcha_payload": captcha_payload,
            },
        )
        assert cross_session.status_code == 422, cross_session.text
        assert cross_session.json()["detail"] == "Verification is invalid or expired"

        accepted = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=headers,
            json={
                "form_data": {"email": "visitor@example.com"},
                "submission_nonce": "nonce-000000000001",
                "honeypot": "",
                "captcha_payload": captcha_payload,
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json() == {
            "mode": "confirmation",
            "status": "accepted",
            "confirmation_markdown": "## All set\n\nWe received it.",
        }

        def find_history_execution():
            response = e2e_client.get(
                "/api/executions",
                params={"workflowId": publishable_form["workflow_id"]},
                headers=platform_admin.headers,
            )
            assert response.status_code == 200, response.text
            return next(
                (
                    execution
                    for execution in response.json()["executions"]
                    if execution["form_id"] == form_id
                ),
                None,
            )

        history_execution = poll_until(
            find_history_execution,
            max_wait=30.0,
            interval=0.2,
        )
        assert history_execution is not None
        assert history_execution["executed_by_name"] == (
            "Public Form · Public form lifecycle"
        )

        duplicate = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=headers,
            json={
                "form_data": {"email": "visitor@example.com"},
                "submission_nonce": "nonce-000000000002",
            },
        )
        assert duplicate.status_code == 409, duplicate.text

        deleted = e2e_client.delete(
            f"/api/forms/{form_id}/publication", headers=platform_admin.headers
        )
        assert deleted.status_code == 204
        assert e2e_client.get(
            f"/api/forms/{form_id}/runtime", headers=headers
        ).status_code == 404
        assert e2e_client.get(
            f"/embed/forms/public/{publication['public_key']}",
            follow_redirects=False,
        ).status_code == 404

    def test_publish_rejects_invalid_origin(
        self, e2e_client, platform_admin, publishable_form
    ):
        form_id = publishable_form["id"]
        review = e2e_client.get(
            f"/api/forms/{form_id}/publication-review",
            headers=platform_admin.headers,
        ).json()

        response = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={
                "reviewed_fingerprint": review["fingerprint"],
                "allowed_origins": ["https://*.example.com"],
            },
        )

        assert response.status_code == 422, response.text

    def test_public_upload_is_field_limited_and_session_owned(
        self, e2e_client, platform_admin, publishable_form
    ):
        form_id = publishable_form["id"]
        update = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={
                "form_schema": {
                    "fields": [
                        {
                            "name": "email",
                            "label": "Email",
                            "type": "email",
                            "required": True,
                        },
                        {
                            "name": "attachment",
                            "label": "Attachment",
                            "type": "file",
                            "allowed_types": ["application/pdf"],
                            "max_size_mb": 1,
                        },
                    ]
                }
            },
        )
        assert update.status_code == 200, update.text
        review = e2e_client.get(
            f"/api/forms/{form_id}/publication-review",
            headers=platform_admin.headers,
        ).json()
        publication = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={"reviewed_fingerprint": review["fingerprint"], "allowed_origins": []},
        ).json()

        def mint_headers():
            bootstrap = e2e_client.get(
                f"/embed/forms/public/{publication['public_key']}",
                follow_redirects=False,
            )
            token = bootstrap.headers["location"].split("#embed_token=")[1]
            return token, {"Authorization": f"Bearer {token}"}

        first_token, first_headers = mint_headers()
        second_token, second_headers = mint_headers()
        first_jti = decode_token(first_token, expected_type="access")["jti"]
        second_jti = decode_token(second_token, expected_type="access")["jti"]

        wrong_field = e2e_client.post(
            f"/api/forms/{form_id}/upload",
            headers=first_headers,
            json={
                "field_name": "email",
                "file_name": "report.pdf",
                "content_type": "application/pdf",
                "file_size": 100,
            },
        )
        assert wrong_field.status_code == 422, wrong_field.text

        other_upload = e2e_client.post(
            f"/api/forms/{form_id}/upload",
            headers=second_headers,
            json={
                "field_name": "attachment",
                "file_name": "report.pdf",
                "content_type": "application/pdf",
                "file_size": 100,
            },
        )
        assert other_upload.status_code == 200, other_upload.text
        other_path = other_upload.json()["blob_uri"]
        assert other_path.startswith(f"{form_id}/{second_jti}/")

        forged = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=first_headers,
            json={
                "form_data": {
                    "email": "visitor@example.com",
                    "attachment": other_path,
                },
                "submission_nonce": "nonce-000000000003",
            },
        )
        assert forged.status_code == 422, forged.text

        invented = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=first_headers,
            json={
                "form_data": {
                    "email": "visitor@example.com",
                    "attachment": f"{form_id}/{first_jti}/invented/report.pdf",
                },
                "submission_nonce": "nonce-000000000005",
            },
        )
        assert invented.status_code == 422, invented.text

        own_upload = e2e_client.post(
            f"/api/forms/{form_id}/upload",
            headers=first_headers,
            json={
                "field_name": "attachment",
                "file_name": "report.pdf",
                "content_type": "application/pdf",
                "file_size": 100,
            },
        )
        assert own_upload.status_code == 200, own_upload.text
        own_path = own_upload.json()["blob_uri"]
        assert own_path.startswith(f"{form_id}/{first_jti}/")

        accepted = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=first_headers,
            json={
                "form_data": {
                    "email": "visitor@example.com",
                    "attachment": own_path,
                },
                "submission_nonce": "nonce-000000000004",
                "captcha_payload": _solve_public_captcha(
                    e2e_client, form_id, first_headers
                ),
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["mode"] == "confirmation"

    def test_html_field_is_a_server_side_publication_blocker(
        self, e2e_client, platform_admin, publishable_form
    ):
        form_id = publishable_form["id"]
        update = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={
                "form_schema": {
                    "fields": [
                        {
                            "name": "unsafe",
                            "type": "html",
                            "content": "<div>{context.field.email}</div>",
                        }
                    ]
                }
            },
        )
        assert update.status_code == 200, update.text

        review = e2e_client.get(
            f"/api/forms/{form_id}/publication-review",
            headers=platform_admin.headers,
        ).json()
        assert [item["code"] for item in review["blockers"]] == ["public_html_field"]

        response = e2e_client.put(
            f"/api/forms/{form_id}/publication",
            headers=platform_admin.headers,
            json={"reviewed_fingerprint": review["fingerprint"], "allowed_origins": []},
        )
        assert response.status_code == 422, response.text
