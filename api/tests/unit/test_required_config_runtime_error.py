"""Unit tests for RequiredConfigUnset — actionable error when a required config has no value."""

import uuid

import pytest
from src.models.enums import ConfigType


@pytest.mark.asyncio
async def test_missing_required_solution_config_raises_actionable_error(
    db_session,
) -> None:
    """A required config with no value, read via require(), must raise RequiredConfigUnset.

    The error must name the key and tell the user how to set it — not a bare
    None/KeyError.
    """
    from src.repositories.config import ConfigRepository, RequiredConfigUnset

    # Unique key: config matching is by (key, org) only, so a generic key like
    # "api_key" can collide with a value another test left in the shared DB.
    key = f"unset_key_{uuid.uuid4().hex[:8]}"
    repo = ConfigRepository(db_session, org_id=None, is_superuser=True)
    with pytest.raises(RequiredConfigUnset) as ei:
        await repo.require(key)

    msg = str(ei.value)
    assert key in msg
    assert "set" in msg.lower()


@pytest.mark.asyncio
async def test_require_returns_value_when_config_exists(db_session) -> None:
    """require() must return the config value when the key is set."""
    from src.models.contracts.config import SetConfigRequest
    from src.repositories.config import ConfigRepository

    repo = ConfigRepository(db_session, org_id=None, is_superuser=True)

    # Seed a global config with key "existing_key"
    await repo.set_config(
        SetConfigRequest(key="existing_key", value="my_value", type=ConfigType.STRING),
        updated_by="test@example.com",
    )

    result = await repo.require("existing_key")
    assert result is not None
