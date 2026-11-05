"""Synthetic SDK boundaries; these do not qualify a live managed identity."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from azure.storage.blob import UserDelegationKey

from src.core.azure_blob_transport import AzureBlobTransport, SyncAzureBlobTransport
from src.core.s3_transport import S3Error
from tests.unit.core.test_azure_blob_transport import settings


@pytest.mark.parametrize("client_id", [None, "00000000-0000-0000-0000-000000000001"])
@pytest.mark.asyncio
async def test_async_identity_selection_and_closure(monkeypatch, client_id):
    import azure.identity.aio
    import azure.storage.blob.aio

    credential = SimpleNamespace(close=AsyncMock())
    identity = MagicMock(return_value=credential)
    service = MagicMock()
    service.close = AsyncMock()
    factory = MagicMock(return_value=service)
    monkeypatch.setattr(azure.identity.aio, "ManagedIdentityCredential", identity)
    monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient", factory)
    config = settings(
        azure_blob_auth="managed_identity",
        azure_blob_account_key=None,
        azure_blob_managed_identity_client_id=client_id,
    )
    async with AzureBlobTransport(config):
        identity.assert_called_once_with(client_id=client_id)
        factory.assert_called_once_with(
            config.azure_blob_account_url, credential=credential
        )
        service.get_container_client.assert_called_once_with("contract")
    service.close.assert_awaited_once()
    credential.close.assert_awaited_once()


def test_sync_identity_selection_and_closure(monkeypatch):
    import azure.identity
    import azure.storage.blob

    credential = MagicMock()
    identity = MagicMock(return_value=credential)
    service = MagicMock()
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", identity)
    monkeypatch.setattr(
        azure.storage.blob, "BlobServiceClient", MagicMock(return_value=service)
    )
    client = SyncAzureBlobTransport(
        settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
    )
    identity.assert_called_once_with(client_id=None)
    client.close()
    service.close.assert_called_once()
    credential.close.assert_called_once()


def test_sync_initialization_failure_releases_owned_identity(monkeypatch):
    credential = MagicMock()
    monkeypatch.setattr(
        "azure.identity.ManagedIdentityCredential", MagicMock(return_value=credential)
    )
    monkeypatch.setattr(
        "azure.storage.blob.BlobServiceClient",
        MagicMock(side_effect=RuntimeError("client construction failed")),
    )
    with pytest.raises(RuntimeError, match="client construction failed"):
        SyncAzureBlobTransport(
            settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
        )
    credential.close.assert_called_once()


@pytest.mark.parametrize("close_fails", [False, True])
def test_sync_close_releases_each_owned_handle_once(monkeypatch, close_fails):
    credential, service = MagicMock(), MagicMock()
    monkeypatch.setattr(
        "azure.identity.ManagedIdentityCredential", MagicMock(return_value=credential)
    )
    monkeypatch.setattr(
        "azure.storage.blob.BlobServiceClient", MagicMock(return_value=service)
    )
    client = SyncAzureBlobTransport(
        settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
    )
    if close_fails:
        service.close.side_effect = RuntimeError("service close failed")
        with pytest.raises(RuntimeError, match="service close failed"):
            client.close()
    else:
        client.close()
    credential.close.assert_called_once()
    client.close()
    service.close.assert_called_once()
    credential.close.assert_called_once()


@pytest.fixture
def delegated_transport():
    client = AzureBlobTransport(
        settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
    )
    client._operation_owned = True
    client._service_client = SimpleNamespace(get_user_delegation_key=AsyncMock())
    client._container_client = MagicMock()
    client._container_client.get_blob_client.return_value.url = (
        "https://example.blob.core.windows.net/contract/signed"
    )
    return client


@pytest.mark.parametrize(
    "operation,permissions", [("get_object", "r"), ("put_object", "cw")]
)
@pytest.mark.asyncio
async def test_delegation_sas_uses_sdk_signer_and_bounded_key_window(
    delegated_transport, monkeypatch, operation, permissions
):

    issued = datetime(2030, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return issued

    monkeypatch.setattr("src.core.azure_blob_transport.datetime", Clock)
    key = UserDelegationKey()
    key.signed_oid = "00000000-0000-0000-0000-000000000001"
    key.signed_tid = "00000000-0000-0000-0000-000000000002"
    key.signed_start = "2030-01-01T00:00:00Z"
    key.signed_expiry = "2030-01-08T00:00:00Z"
    key.signed_service = "b"
    key.signed_version = "2025-11-05"
    # Public synthetic HMAC material, with no Azure identity or authority.
    key.value = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    issuer = delegated_transport._service_client.get_user_delegation_key
    issuer.return_value = key
    url = await delegated_transport.generate_presigned_url(
        operation,
        Params={"Bucket": "contract", "Key": "signed"},
        ExpiresIn=604800,
    )
    issuer.assert_awaited_once_with(
        key_start_time=issued, key_expiry_time=issued + timedelta(days=7)
    )
    query = parse_qs(urlsplit(url).query)
    assert query["sp"] == [permissions]
    assert query["se"] == [key.signed_expiry]
    assert query["skoid"] == [key.signed_oid]
    assert query["sktid"] == [key.signed_tid]
    assert query["sr"] == ["b"]
    assert query["sig"]


@pytest.mark.parametrize("expiry", [0, -1, 604801])
@pytest.mark.asyncio
async def test_invalid_sas_window_never_requests_delegation(
    delegated_transport, expiry
):
    with pytest.raises(ValueError, match="SAS lifetime"):
        await delegated_transport.generate_presigned_url(
            "get_object",
            Params={"Bucket": "contract", "Key": "signed"},
            ExpiresIn=expiry,
        )
    delegated_transport._service_client.get_user_delegation_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegation_denial_is_not_replaced_by_another_credential(
    delegated_transport,
):
    from azure.core.exceptions import HttpResponseError

    denied = HttpResponseError(message="private issuer detail")
    denied.status_code = 403
    denied.error_code = "AuthorizationPermissionMismatch"
    issuer = delegated_transport._service_client.get_user_delegation_key
    issuer.side_effect = denied
    with pytest.raises(S3Error) as raised:
        await delegated_transport.generate_presigned_url(
            "get_object", Params={"Bucket": "contract", "Key": "signed"}
        )
    assert raised.value.code == "AccessDenied"
    assert raised.value.status_code == 403
    assert "private issuer detail" not in str(raised.value)
    issuer.assert_awaited_once()
