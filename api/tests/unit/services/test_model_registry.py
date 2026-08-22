from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.model_registry import _fetch_model_mapping, normalize_model_name


@pytest.mark.asyncio
async def test_google_model_mapping_uses_native_model_list() -> None:
    model = MagicMock()
    model.name = "models/gemini-2.5-flash"
    model.display_name = "Gemini 2.5 Flash"
    google_client = MagicMock()
    google_client.aio.models.list = AsyncMock(return_value=MagicMock(page=[model]))
    google_client.aio.aclose = AsyncMock()

    with patch("google.genai.Client", return_value=google_client):
        mapping = await _fetch_model_mapping("google", "test-key")

    assert mapping == {"gemini-2.5-flash": "Gemini 2.5 Flash"}
    google_client.aio.aclose.assert_awaited_once()


class TestNormalizeModelName:
    @pytest.mark.parametrize(
        "model_id, expected",
        [
            # Anthropic format: -YYYYMMDD
            ("claude-opus-4-5-20251101", "claude-opus-4-5"),
            ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
            ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet"),
            ("claude-sonnet-4-5-20250514", "claude-sonnet-4-5"),
            # OpenAI format: -YYYY-MM-DD
            ("gpt-4o-2024-11-20", "gpt-4o"),
            ("gpt-4-turbo-2024-04-09", "gpt-4-turbo"),
            ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
            # No date suffix - unchanged
            ("gpt-4", "gpt-4"),
            ("gpt-4o", "gpt-4o"),
            ("claude-opus-4-5", "claude-opus-4-5"),
            ("o1-mini", "o1-mini"),
            # Edge cases
            ("model", "model"),
            ("a-20251231", "a"),
            ("a-2025-12-31", "a"),
        ],
    )
    def test_strips_date_suffix(self, model_id, expected):
        assert normalize_model_name(model_id) == expected

    def test_does_not_strip_partial_date(self):
        # Only 6 digits after dash, not 8 - should not match YYYYMMDD
        assert normalize_model_name("model-202511") == "model-202511"

    def test_does_not_strip_incomplete_iso_date(self):
        # Missing day part
        assert normalize_model_name("model-2025-11") == "model-2025-11"

    def test_date_in_middle_not_stripped(self):
        # Date suffix pattern only matches at end of string
        assert normalize_model_name("model-20251101-extra") == "model-20251101-extra"

    def test_empty_string(self):
        assert normalize_model_name("") == ""

    def test_no_hyphens(self):
        assert normalize_model_name("gpt4") == "gpt4"
