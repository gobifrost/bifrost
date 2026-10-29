"""The executor keeps every database connection it may open."""

from __future__ import annotations

from src.config import Settings
from src.worker.pool_main import executor_database_settings


def test_overflow_becomes_pooled_and_the_total_is_unchanged() -> None:
    settings = Settings(database_pool_size=1, database_max_overflow=2)

    sized = executor_database_settings(settings)

    assert sized.database_pool_size == 3
    assert sized.database_max_overflow == 0
    assert settings.database_pool_size == 1, "the shared settings are not changed"
