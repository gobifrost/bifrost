"""
LLM Configuration E2E Tests.

Tests the complete LLM configuration workflow including:
- Provider configuration (OpenAI, Anthropic, custom)
- Connection testing with real API keys
- Model listing
- Access control

Requirements:
- At least one of these environment variables should be set:
  - ANTHROPIC_API_TEST_KEY: Anthropic API key
  - OPENAPI_API_TEST_KEY: OpenAI API key
  - GENERIC_AI_TEST_KEY + GENERIC_AI_BASE_URL: Custom provider

Tests skip gracefully if environment variables are not configured.
"""

import logging

import pytest

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Tests
# =============================================================================


class TestLLMConfigurationCRUD:
    """Test LLM configuration CRUD operations."""

    def test_get_config_not_configured(
        self,
        e2e_client,
        platform_admin,
        llm_config_cleanup,
    ):
        """Test getting config when LLM is not configured."""
        response = e2e_client.get(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Should return null/None when not configured
        data = response.json()
        assert data is None

    def test_set_anthropic_config(
        self,
        e2e_client,
        platform_admin,
        llm_test_anthropic_key,
        llm_config_cleanup,
    ):
        """Test setting Anthropic as LLM provider."""
        if not llm_test_anthropic_key:
            pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

        response = e2e_client.post(
            "/api/admin/llm/config",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": llm_test_anthropic_key,
                "max_tokens": 2048,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Set config failed: {response.text}"

        data = response.json()
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-haiku-4-5-20251001"
        assert data["max_tokens"] == 2048
        assert data["is_configured"] is True
        assert data["api_key_set"] is True
        # API key should NOT be returned
        assert "api_key" not in data

    def test_set_openai_config(
        self,
        e2e_client,
        platform_admin,
        llm_test_openai_key,
        llm_config_cleanup,
    ):
        """Test setting OpenAI as LLM provider."""
        if not llm_test_openai_key:
            pytest.skip("OPENAPI_API_TEST_KEY not configured")

        response = e2e_client.post(
            "/api/admin/llm/config",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": llm_test_openai_key,
                "max_tokens": 4096,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Set config failed: {response.text}"

        data = response.json()
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4o-mini"
        assert data["is_configured"] is True
        assert data["api_key_set"] is True

    def test_set_custom_provider_config(
        self,
        e2e_client,
        platform_admin,
        llm_test_custom_config,
        llm_config_cleanup,
    ):
        """Test setting custom OpenAI-compatible provider (e.g., DeepSeek)."""
        if not llm_test_custom_config:
            pytest.skip("GENERIC_AI_TEST_KEY/GENERIC_AI_BASE_URL not configured")

        response = e2e_client.post(
            "/api/admin/llm/config",
            json={
                "provider": "openai",
                "model": "deepseek-chat",
                "api_key": llm_test_custom_config["api_key"],
                "endpoint": llm_test_custom_config["endpoint"],
                "max_tokens": 2048,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Set config failed: {response.text}"

        data = response.json()
        assert data["provider"] == "openai"
        assert data["model"] == "deepseek-chat"
        assert data["endpoint"] == llm_test_custom_config["endpoint"]
        assert data["is_configured"] is True
        assert data["api_key_set"] is True

    @pytest.mark.asyncio
    async def test_update_config_overwrites(
        self,
        e2e_client,
        platform_admin,
        llm_test_anthropic_key,
        llm_config_cleanup,
        db_session,
    ):
        """Test that setting config again overwrites the previous config."""
        if not llm_test_anthropic_key:
            pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

        # Seed the existing row directly. Other tests cover the live provider
        # gate; this test needs one external call, for the update under test,
        # rather than becoming flaky on two consecutive provider validations.
        from src.services.llm_config_service import LLMConfigService

        service = LLMConfigService(db_session)
        await service.save_config(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            api_key=llm_test_anthropic_key,
            max_tokens=1024,
            updated_by=platform_admin.email,
        )
        await db_session.commit()

        # Update a stable setting while keeping the already-validated model.
        # The purpose of this test is overwrite semantics, not availability of
        # a second provider model (which changes independently of Bifrost).
        response = e2e_client.post(
            "/api/admin/llm/config",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": llm_test_anthropic_key,
                "max_tokens": 4096,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Verify updated values
        response = e2e_client.get(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "claude-haiku-4-5-20251001"
        assert data["max_tokens"] == 4096

    def test_get_config_after_set(
        self,
        e2e_client,
        platform_admin,
        llm_anthropic_configured,
    ):
        """Test getting config after it's been set."""
        response = e2e_client.get(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["provider"] == "anthropic"
        assert data["is_configured"] is True
        assert data["api_key_set"] is True
        # API key should not be returned in GET
        assert "api_key" not in data or data.get("api_key") is None

    def test_delete_config(
        self,
        e2e_client,
        platform_admin,
        llm_anthropic_configured,
    ):
        """Test deleting LLM config."""
        # First verify it exists
        response = e2e_client.get(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json() is not None

        # Delete it
        response = e2e_client.delete(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify it's gone
        response = e2e_client.get(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json() is None

    def test_delete_config_not_found(
        self,
        e2e_client,
        platform_admin,
        llm_config_cleanup,
    ):
        """Test deleting config when none exists."""
        response = e2e_client.delete(
            "/api/admin/llm/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


# =============================================================================
# Connection Test Endpoints
# =============================================================================


class TestLLMConnectionTests:
    """Test LLM connection testing with real API keys."""

    def test_test_anthropic_connection(
        self,
        e2e_client,
        platform_admin,
        llm_test_anthropic_key,
        llm_config_cleanup,
    ):
        """Test connection to Anthropic with valid API key."""
        if not llm_test_anthropic_key:
            pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

        response = e2e_client.post(
            "/api/admin/llm/test",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": llm_test_anthropic_key,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Test connection failed: {response.text}"

        data = response.json()
        assert data["success"] is True
        assert "Connected to" in data["message"]
        assert data["models"] is not None
        assert len(data["models"]) > 0

    def test_test_openai_connection(
        self,
        e2e_client,
        platform_admin,
        llm_test_openai_key,
        llm_config_cleanup,
    ):
        """Test connection to OpenAI with valid API key."""
        if not llm_test_openai_key:
            pytest.skip("OPENAPI_API_TEST_KEY not configured")

        response = e2e_client.post(
            "/api/admin/llm/test",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": llm_test_openai_key,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Test connection failed: {response.text}"

        data = response.json()
        assert data["success"] is True
        assert "Connected to" in data["message"]
        assert data["models"] is not None
        assert len(data["models"]) > 0

    def test_test_connection_invalid_key(
        self,
        e2e_client,
        platform_admin,
        llm_config_cleanup,
    ):
        """Test connection with invalid API key fails gracefully."""
        response = e2e_client.post(
            "/api/admin/llm/test",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": "invalid-api-key-12345",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200  # Endpoint returns 200 with success=False

        data = response.json()
        assert data["success"] is False
        assert "failed" in data["message"].lower() or "error" in data["message"].lower()

    def test_test_saved_connection(
        self,
        e2e_client,
        platform_admin,
        llm_anthropic_configured,
    ):
        """Test connection using saved configuration."""
        response = e2e_client.post(
            "/api/admin/llm/test-saved",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, (
            f"Test saved connection failed: {response.text}"
        )

        data = response.json()
        assert data["success"] is True
        assert "Connected" in data["message"]

    def test_test_saved_connection_not_configured(
        self,
        e2e_client,
        platform_admin,
        llm_config_cleanup,
    ):
        """Test saved connection when no config exists returns 404."""
        response = e2e_client.post(
            "/api/admin/llm/test-saved",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


# =============================================================================
# Model Listing
# =============================================================================


class TestLLMModelListing:
    """Test LLM model listing endpoint."""

    def test_list_models_anthropic(
        self,
        e2e_client,
        platform_admin,
        llm_anthropic_configured,
    ):
        """Test listing models from Anthropic provider."""
        response = e2e_client.get(
            "/api/admin/llm/models",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List models failed: {response.text}"

        data = response.json()
        assert "models" in data
        assert "provider" in data
        assert data["provider"] == "anthropic"
        assert len(data["models"]) > 0
        # Should include known Anthropic models
        model_names = data["models"]
        assert any("claude" in m["id"].lower() for m in model_names)

    def test_list_models_openai(
        self,
        e2e_client,
        platform_admin,
        llm_openai_configured,
    ):
        """Test listing models from OpenAI provider."""
        response = e2e_client.get(
            "/api/admin/llm/models",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List models failed: {response.text}"

        data = response.json()
        assert "models" in data
        assert "provider" in data
        assert data["provider"] == "openai"
        assert len(data["models"]) > 0
        # Should include GPT models
        model_names = data["models"]
        assert any("gpt" in m["id"].lower() for m in model_names)

    def test_list_models_not_configured(
        self,
        e2e_client,
        platform_admin,
        llm_config_cleanup,
    ):
        """Test listing models when no provider is configured."""
        response = e2e_client.get(
            "/api/admin/llm/models",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


# =============================================================================
# Access Control Tests
# =============================================================================


class TestLLMAccessControl:
    """Test access control for LLM admin endpoints."""

    def test_org_user_cannot_get_config(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot access LLM config."""
        response = e2e_client.get(
            "/api/admin/llm/config",
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_set_config(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot set LLM config."""
        response = e2e_client.post(
            "/api/admin/llm/config",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": "fake-key",
            },
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_delete_config(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot delete LLM config."""
        response = e2e_client.delete(
            "/api/admin/llm/config",
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_test_connection(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot test LLM connections."""
        response = e2e_client.post(
            "/api/admin/llm/test",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": "fake-key",
            },
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_list_models(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot list models."""
        response = e2e_client.get(
            "/api/admin/llm/models",
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_unauthenticated_cannot_access(
        self,
        e2e_client,
    ):
        """Test that unauthenticated requests are rejected."""
        response = e2e_client.get("/api/admin/llm/config")
        assert response.status_code in [401, 403, 422]

        response = e2e_client.post(
            "/api/admin/llm/config",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": "fake-key",
            },
        )
        # POST without auth triggers CSRF validation which may return 500 due to
        # middleware error handling. The key is that it doesn't return 200/201.
        assert response.status_code in [401, 403, 422, 500]
