import pytest
from pydantic import ValidationError

from src.models.contracts.run_retention import RunRetentionSettings, RunRetentionSettingsUpdate


def test_default_is_thirty_days():
    assert RunRetentionSettings().days == 30


@pytest.mark.parametrize("days", [30, 365, 3650, None])
def test_accepts_window_or_forever(days):
    assert RunRetentionSettingsUpdate(days=days).days == days


@pytest.mark.parametrize("days", [0, 29, 3651])
def test_rejects_out_of_range(days):
    with pytest.raises(ValidationError):
        RunRetentionSettingsUpdate(days=days)


def test_update_requires_days_key():
    with pytest.raises(ValidationError):
        RunRetentionSettingsUpdate.model_validate({})
