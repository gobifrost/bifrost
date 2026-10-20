"""
Contract Tests for Data Provider API
Tests data provider response format and validation
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError, field_validator


class DataProviderOption(BaseModel):
    """Single option returned by data provider"""
    label: str
    value: str
    metadata: dict[str, Any] | None = None

    @field_validator('label')
    @classmethod
    def label_not_empty(cls, v):
        """Ensure label is not empty"""
        if not v or not v.strip():
            raise ValueError("Label cannot be empty")
        return v

    @field_validator('value')
    @classmethod
    def value_not_empty(cls, v):
        """Ensure value is not empty"""
        if not v or not v.strip():
            raise ValueError("Value cannot be empty")
        return v


class DataProviderResponse(BaseModel):
    """Response from data provider endpoint"""
    provider: str
    options: list[DataProviderOption]
    cached: bool = False
    cache_expires_at: str | None = None


class TestDataProviderOption:
    """Test DataProviderOption model"""

    def test_valid_option_minimal(self):
        """Test valid option with only required fields"""
        option = DataProviderOption(
            label="Microsoft 365 E3",
            value="SPE_E3"
        )

        assert option.label == "Microsoft 365 E3"
        assert option.value == "SPE_E3"
        assert option.metadata is None

    def test_valid_option_with_metadata(self):
        """Test valid option with metadata"""
        option = DataProviderOption(
            label="Microsoft 365 E3",
            value="SPE_E3",
            metadata={
                "available": 20,
                "total": 50,
                "consumed": 30
            }
        )

        assert option.label == "Microsoft 365 E3"
        assert option.value == "SPE_E3"
        assert option.metadata["available"] == 20
        assert option.metadata["total"] == 50

    def test_empty_label_raises_error(self):
        """Test that empty label raises validation error"""
        with pytest.raises(ValidationError) as exc_info:
            DataProviderOption(
                label="",
                value="SPE_E3"
            )

        errors = exc_info.value.errors()
        assert any(e['loc'] == ('label',) for e in errors)

    def test_empty_value_raises_error(self):
        """Test that empty value raises validation error"""
        with pytest.raises(ValidationError) as exc_info:
            DataProviderOption(
                label="Microsoft 365 E3",
                value=""
            )

        errors = exc_info.value.errors()
        assert any(e['loc'] == ('value',) for e in errors)

    def test_missing_label_raises_error(self):
        """Test that missing label raises validation error"""
        with pytest.raises(ValidationError) as exc_info:
            DataProviderOption(value="SPE_E3")

        errors = exc_info.value.errors()
        assert any(e['loc'] == ('label',) for e in errors)

    def test_missing_value_raises_error(self):
        """Test that missing value raises validation error"""
        with pytest.raises(ValidationError) as exc_info:
            DataProviderOption(label="Microsoft 365 E3")

        errors = exc_info.value.errors()
        assert any(e['loc'] == ('value',) for e in errors)

    def test_metadata_can_be_complex(self):
        """Test that metadata can contain complex nested structures"""
        option = DataProviderOption(
            label="Test License",
            value="TEST_SKU",
            metadata={
                "pricing": {
                    "monthly": 20.00,
                    "annual": 200.00
                },
                "features": ["Exchange", "OneDrive", "Teams"],
                "limits": {
                    "storage_gb": 1024,
                    "max_users": 300
                }
            }
        )

        assert option.metadata["pricing"]["monthly"] == 20.00
        assert "Teams" in option.metadata["features"]
        assert option.metadata["limits"]["storage_gb"] == 1024


class TestDataProviderResponse:
    """Test DataProviderResponse model"""

    def test_valid_response_minimal(self):
        """Test valid response with minimal fields"""
        response = DataProviderResponse(
            provider="get_available_licenses",
            options=[
                DataProviderOption(label="E3", value="SPE_E3"),
                DataProviderOption(label="E5", value="SPE_E5")
            ]
        )

        assert response.provider == "get_available_licenses"
        assert len(response.options) == 2
        assert response.cached is False
        assert response.cache_expires_at is None

    def test_valid_response_with_cache_info(self):
        """Test valid response with cache information"""
        response = DataProviderResponse(
            provider="get_available_licenses",
            options=[
                DataProviderOption(label="E3", value="SPE_E3")
            ],
            cached=True,
            cache_expires_at="2025-10-10T12:00:00Z"
        )

        assert response.provider == "get_available_licenses"
        assert response.cached is True
        assert response.cache_expires_at == "2025-10-10T12:00:00Z"

    def test_empty_options_list(self):
        """Test that empty options list is valid"""
        response = DataProviderResponse(
            provider="get_available_licenses",
            options=[]
        )

        assert response.provider == "get_available_licenses"
        assert len(response.options) == 0

    def test_missing_provider_raises_error(self):
        """Test that missing provider raises validation error"""
        with pytest.raises(ValidationError) as exc_info:
            DataProviderResponse(
                options=[DataProviderOption(label="E3", value="SPE_E3")]
            )

        errors = exc_info.value.errors()
        assert any(e['loc'] == ('provider',) for e in errors)

    def test_missing_options_raises_error(self):
        """Test that missing options raises validation error"""
        with pytest.raises(ValidationError) as exc_info:
            DataProviderResponse(provider="get_available_licenses")

        errors = exc_info.value.errors()
        assert any(e['loc'] == ('options',) for e in errors)

    def test_options_with_metadata(self):
        """Test response with options containing metadata"""
        response = DataProviderResponse(
            provider="get_available_licenses",
            options=[
                DataProviderOption(
                    label="Microsoft 365 E3",
                    value="SPE_E3",
                    metadata={"available": 20, "total": 50}
                ),
                DataProviderOption(
                    label="Microsoft 365 E5",
                    value="SPE_E5",
                    metadata={"available": 3, "total": 5}
                )
            ]
        )

        assert len(response.options) == 2
        assert response.options[0].metadata["available"] == 20
        assert response.options[1].metadata["available"] == 3


class TestDataProviderExamples:
    """Test realistic data provider examples"""

    def test_license_provider_response(self):
        """Test realistic license provider response"""
        response = DataProviderResponse(
            provider="get_available_licenses",
            options=[
                DataProviderOption(
                    label="Microsoft 365 Business Basic",
                    value="O365_BUSINESS_ESSENTIALS",
                    metadata={
                        "available": 10,
                        "total": 25,
                        "consumed": 15
                    }
                ),
                DataProviderOption(
                    label="Microsoft 365 E3",
                    value="SPE_E3",
                    metadata={
                        "available": 20,
                        "total": 50,
                        "consumed": 30
                    }
                )
            ],
            cached=False
        )

        assert response.provider == "get_available_licenses"
        assert len(response.options) == 2

        # Verify first option
        e_basic = response.options[0]
        assert e_basic.label == "Microsoft 365 Business Basic"
        assert e_basic.value == "O365_BUSINESS_ESSENTIALS"
        assert e_basic.metadata["available"] == 10

        # Verify second option
        e3 = response.options[1]
        assert e3.label == "Microsoft 365 E3"
        assert e3.metadata["total"] == 50

    def test_user_groups_provider_response(self):
        """Test realistic user groups provider response"""
        response = DataProviderResponse(
            provider="get_user_groups",
            options=[
                DataProviderOption(
                    label="IT Department",
                    value="group-it-001",
                    metadata={"member_count": 12, "type": "security"}
                ),
                DataProviderOption(
                    label="Sales Team",
                    value="group-sales-002",
                    metadata={"member_count": 45, "type": "distribution"}
                )
            ]
        )

        assert len(response.options) == 2
        assert response.options[0].metadata["type"] == "security"
        assert response.options[1].metadata["member_count"] == 45

    def test_cached_response(self):
        """Test cached data provider response"""
        response = DataProviderResponse(
            provider="get_available_licenses",
            options=[
                DataProviderOption(label="E3", value="SPE_E3")
            ],
            cached=True,
            cache_expires_at="2025-10-10T12:05:00Z"
        )

        assert response.cached is True
        assert response.cache_expires_at is not None


@pytest.mark.asyncio
async def test_engine_provider_cache_transport_sends_only_parameters_and_result(monkeypatch):
    """The child cannot choose cache scope, workflow, user, or TTL."""
    from bifrost import client
    from src.services.execution.engine import (
        _read_runtime_data_provider_cache,
        _write_runtime_data_provider_cache,
    )

    read_response = SimpleNamespace(
        is_success=True,
        status_code=200,
        json=lambda: {"hit": True, "result": ["contoso"], "expires_at": "2026-10-17T12:00:00+00:00"},
    )
    write_response = SimpleNamespace(
        is_success=True,
        status_code=200,
        json=lambda: {"expires_at": "2026-10-17T12:00:00+00:00"},
    )
    engine_request = AsyncMock(side_effect=[read_response, write_response])
    monkeypatch.setattr(client, "get_client", lambda: SimpleNamespace(engine_request=engine_request))

    assert await _read_runtime_data_provider_cache({"tenant": "contoso"}) == {
        "data": ["contoso"], "expires_at": "2026-10-17T12:00:00+00:00",
    }
    assert await _write_runtime_data_provider_cache({"tenant": "contoso"}, ["contoso"]) == "2026-10-17T12:00:00+00:00"
    assert engine_request.await_args_list[0].args == ("POST", "/api/sdk/runtime/data-provider-cache/read")
    assert engine_request.await_args_list[0].kwargs["json"] == {"parameters": {"tenant": "contoso"}}
    assert engine_request.await_args_list[1].kwargs["json"] == {
        "parameters": {"tenant": "contoso"}, "result": ["contoso"],
    }
