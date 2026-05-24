"""Tests for ConfigResolver with optional session parameter."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


class TestConfigResolverWithSession:
    """Test ConfigResolver methods with optional session."""

    @pytest.fixture
    def resolver(self):
        """Create ConfigResolver instance."""
        from src.core.config_resolver import ConfigResolver
        return ConfigResolver()

    @pytest.mark.asyncio
    async def test_get_organization_uses_provided_session(self, resolver):
        """get_organization() should use provided session on cache miss."""
        org_id = str(uuid4())

        # Mock cache miss
        resolver._get_org_from_cache = AsyncMock(return_value=None)
        resolver._set_org_cache = AsyncMock()

        mock_org = MagicMock()
        mock_org.id = uuid4()
        mock_org.name = "Test Org"
        mock_org.is_active = True
        mock_org.domain = "test.com"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_org

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await resolver.get_organization(org_id, db=mock_session)

        assert result is not None
        assert result.name == "Test Org"
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_config_for_scope_uses_provided_session(self, resolver):
        """load_config_for_scope() should use provided session on cache miss."""
        org_id = str(uuid4())

        # Mock cache miss
        resolver._get_config_from_cache = AsyncMock(return_value=None)
        resolver._set_config_cache = AsyncMock()

        mock_config = MagicMock()
        mock_config.key = "test_key"
        mock_config.value = {"value": "test_value"}
        mock_config.config_type = MagicMock(value="string")

        # Mock scalars() to return iterable
        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter([mock_config]))

        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await resolver.load_config_for_scope(f"ORG:{org_id}", db=mock_session)

        assert "test_key" in result
        mock_session.execute.assert_called()

    @pytest.mark.asyncio
    async def test_load_config_for_org_scope_omits_global_secrets(self, resolver):
        """Org-scoped CLI config fallback must not inherit global secrets."""
        from src.models.enums import ConfigType

        org_id = str(uuid4())
        resolver._get_config_from_cache = AsyncMock(return_value=None)
        resolver._set_config_cache = AsyncMock()

        global_secret = MagicMock()
        global_secret.key = "api_key"
        global_secret.value = {"value": "encrypted-global-secret"}
        global_secret.config_type = ConfigType.SECRET

        global_plain = MagicMock()
        global_plain.key = "base_url"
        global_plain.value = {"value": "https://example.test"}
        global_plain.config_type = ConfigType.STRING

        org_secret = MagicMock()
        org_secret.key = "org_api_key"
        org_secret.value = {"value": "encrypted-org-secret"}
        org_secret.config_type = ConfigType.SECRET

        def _result(entries):
            scalars = MagicMock()
            scalars.__iter__ = MagicMock(return_value=iter(entries))
            result = MagicMock()
            result.scalars.return_value = scalars
            return result

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _result([global_secret, global_plain]),
                _result([org_secret]),
            ]
        )

        result = await resolver.load_config_for_scope(f"ORG:{org_id}", db=mock_session)

        assert "api_key" not in result
        assert result["base_url"]["value"] == "https://example.test"
        assert result["base_url"]["scope"] == "global"
        assert result["org_api_key"]["value"] == "encrypted-org-secret"
        assert result["org_api_key"]["scope"] == "org"
