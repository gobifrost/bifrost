"""Synthetic Azure Blob boundaries; these do not qualify a live identity."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from src.config import Settings
from src.core.storage import (
    object_store, storage_configured, storage_identity, sync_object_store,
    workspace_mirror,
)
from src.core.storage.azure import (
    AzureObjectStore, SyncAzureObjectStore, _content_settings, _metadata,
    _service_error, _unmetadata, _version,
)
from src.core.storage.azure_mirror import AzureWorkspaceMirror
from src.core.storage.contracts import (
    ContentProperties, CopyOptions, ObjectVersion, StorageConfigurationError,
    StorageError, WriteOptions,
)
from src.core.storage.s3 import S3ObjectStore


LOCAL_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "object_storage_provider": "azure_blob",
            "azure_blob_account_url": "https://example.blob.core.windows.net",
            "azure_blob_container": "contract",
            "azure_blob_auth": "account_key",
            "azure_blob_account_key": SecretStr("local-fixture"),
            **overrides,
        },
    )


def s3_settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "s3_bucket": "fixture",
            "s3_endpoint_url": "https://storage.example",
            "s3_access_key": "fixture-access",
            "s3_secret_key": "fixture-secret",
            **overrides,
        },
    )


def _props(**overrides):
    values = {
        "etag": '"version"',
        "size": 3,
        "last_modified": None,
        "metadata": {},
        "content_settings": SimpleNamespace(
            content_type="text/plain", content_disposition=None,
            content_encoding=None, content_language=None, cache_control=None,
            content_md5=None,
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def store():
    store = AzureObjectStore("contract", settings())
    store._container = MagicMock()
    return store


# Configuration ---------------------------------------------------------------


def test_s3_remains_default_and_azure_selects_blob_store():
    assert s3_settings().object_storage_provider == "s3"
    assert isinstance(object_store(s3_settings()), S3ObjectStore)
    assert isinstance(object_store(settings()), AzureObjectStore)
    with pytest.raises(StorageConfigurationError):
        object_store(settings(azure_blob_container=None))
    assert isinstance(sync_object_store(settings()), SyncAzureObjectStore)
    assert isinstance(workspace_mirror(settings()), AzureWorkspaceMirror)
    assert not workspace_mirror(settings()).follows_symlinks
    assert storage_configured(settings())
    assert not storage_configured(settings(azure_blob_container=None))
    assert storage_configured(s3_settings())
    assert settings().storage_bucket == "contract"
    assert s3_settings().storage_bucket == "fixture"


def test_provider_identity_covers_every_credential_bearing_field():
    base = storage_identity(settings())
    assert isinstance(base, tuple) and base[0] == "azure_blob"
    assert storage_identity(settings(azure_blob_container="other")) != base
    assert storage_identity(settings(azure_blob_account_url="https://other.blob.core.windows.net")) != base
    s3_base = storage_identity(s3_settings())
    assert isinstance(s3_base, tuple) and s3_base[0] == "s3"


@pytest.mark.parametrize("suffix", [
    "blob.core.windows.net", "blob.core.usgovcloudapi.net", "blob.core.chinacloudapi.cn",
])
def test_account_name_is_derived_from_standard_endpoint(suffix):
    assert AzureObjectStore._account_name(settings(
        azure_blob_account_url=f"https://example.{suffix}",
    )) == "example"


def test_custom_endpoint_requires_explicit_account_name():
    config = settings(azure_blob_account_url="https://storage.example.com")
    with pytest.raises(StorageConfigurationError, match="account name is required"):
        AzureObjectStore._account_name(config)
    assert AzureObjectStore._account_name(settings(
        azure_blob_account_url="https://storage.example.com", azure_blob_account_name="example",
    )) == "example"


def test_managed_identity_never_accepts_an_explicit_key():
    assert not settings(azure_blob_auth="managed_identity").azure_blob_configured
    assert settings(
        azure_blob_auth="managed_identity", azure_blob_account_key=None
    ).azure_blob_configured
    assert settings(azure_blob_auth="account_key").azure_blob_configured
    assert not settings(azure_blob_auth="account_key", azure_blob_account_key=None).azure_blob_configured


def test_blob_settings_do_not_serialize_keys_or_accept_url_credentials():
    assert "azure_blob_account_key" not in settings().model_dump()
    with pytest.raises(ValidationError) as raised:
        settings(azure_blob_account_url="https://example.blob.core.windows.net?sig=secret")
    assert "sig=secret" not in str(raised.value)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "azurite"])
def test_local_emulator_may_use_plain_http(host):
    assert settings(azure_blob_account_url=f"http://{host}:10000/devstoreaccount1").azure_blob_account_url


def test_plain_http_is_rejected_outside_local_emulators():
    with pytest.raises(ValidationError):
        settings(azure_blob_account_url="http://example.blob.core.windows.net")


# Versions and metadata --------------------------------------------------------


@pytest.mark.parametrize("etag,expected", [('"abc"', '"abc"'), ("abc", '"abc"')])
def test_version_normalizes_quoting(etag, expected):
    assert _version(etag) == expected


def test_version_requires_a_token():
    with pytest.raises(StorageError) as raised:
        _version("")
    assert raised.value.kind == "backend_error"


def test_versions_serialize_verbatim():
    store = AzureObjectStore("contract", settings())
    assert store.serialize_version(ObjectVersion('"abc"')) == '"abc"'
    assert store.restore_version('"abc"') == '"abc"'
    with pytest.raises(StorageError):
        store.serialize_version(ObjectVersion(""))


def test_move_marker_metadata_round_trips_without_azure_name_loss():
    values = {"bifrost-move-row": "row", "UpperCase": "é", "bifrost_metadata": "user value"}
    assert _unmetadata(_metadata(values)) == {key.lower(): value for key, value in values.items()}


def test_metadata_rejects_case_collisions():
    with pytest.raises(ValueError):
        _metadata({"Name": "a", "name": "b"})


def test_unmetadata_rejects_mixed_envelopes():
    with pytest.raises(StorageError):
        _unmetadata({**_metadata({"a": "b"}), "plain": "c"})


def test_expires_is_an_explicit_limitation_not_a_silent_drop():
    with pytest.raises(StorageError) as raised:
        _content_settings(ContentProperties(http_expires="Wed, 21 Oct 2030 07:28:00 GMT"))
    assert raised.value.kind == "unsupported"


# Error mapping ----------------------------------------------------------------


def _failure(status_code, error_code):
    from azure.core.exceptions import HttpResponseError

    error = HttpResponseError(message="private detail")
    error.status_code = status_code
    error.error_code = error_code
    return error


@pytest.mark.parametrize("status_code,error_code,kind", [
    (404, "BlobNotFound", "not_found"),
    (404, "ContainerNotFound", "namespace_not_found"),
    (412, "ConditionNotMet", "precondition_failed"),
    (409, "BlobAlreadyExists", "precondition_failed"),
    (409, "LeaseAlreadyPresent", "conflict"),
    (401, "InvalidAuthenticationInfo", "permission_denied"),
    (403, "AuthorizationPermissionMismatch", "permission_denied"),
    (503, "ServerBusy", "unavailable"),
    (500, "InternalError", "unavailable"),
    (501, "APINotImplemented", "unsupported"),
])
def test_status_maps_to_a_stable_kind(status_code, error_code, kind):
    assert _service_error(_failure(status_code, error_code)).kind == kind

def test_transport_failures_are_unavailable_without_leaking_detail():
    from azure.core.exceptions import ServiceRequestError

    error = _service_error(ServiceRequestError("private detail"))
    assert error.kind == "unavailable"
    assert "private detail" not in str(error)


@pytest.mark.asyncio
async def test_missing_blob_and_missing_container_are_distinct(store):
    from azure.core.exceptions import ResourceNotFoundError

    blob = store._container.get_blob_client.return_value
    missing = ResourceNotFoundError(message="private URL")
    missing.error_code = "BlobNotFound"
    missing.status_code = 404
    blob.get_blob_properties = AsyncMock(side_effect=missing)
    with pytest.raises(StorageError) as raised:
        await store.stat("key")
    assert raised.value.kind == "not_found"
    assert "private URL" not in str(raised.value)
    missing_container = ResourceNotFoundError(message="private URL")
    missing_container.error_code = "ContainerNotFound"
    missing_container.status_code = 404
    blob.get_blob_properties.side_effect = missing_container
    with pytest.raises(StorageError) as raised:
        await store.stat("key")
    assert raised.value.kind == "namespace_not_found"


# Reads and writes -------------------------------------------------------------


@pytest.mark.asyncio
async def test_stat_returns_contract_info(store):
    blob = store._container.get_blob_client.return_value
    blob.get_blob_properties = AsyncMock(return_value=_props(
        metadata={"bifrost_metadata": _metadata({"X-Row": "row"})["bifrost_metadata"]},
    ))
    info = await store.stat("key")
    assert info.key == "key"
    assert info.version == '"version"'
    assert info.size == 3
    assert info.metadata == {"x-row": "row"}
    assert info.properties.content_type == "text/plain"
    assert info.http_etag == '"version"'


@pytest.mark.asyncio
async def test_probe_uses_container_properties(store):
    store._container.get_container_properties = AsyncMock()
    await store.probe()
    store._container.get_container_properties.assert_awaited_once()


def _stream(chunks, properties):
    stream = SimpleNamespace(properties=properties)

    async def gen():
        for chunk in chunks:
            yield chunk

    stream.chunks = gen
    return stream


@pytest.mark.asyncio
async def test_open_read_verifies_version_atomically(store):
    from azure.core import MatchConditions

    blob = store._container.get_blob_client.return_value
    observed = _stream([b"abc"], _props())

    async def conditional_download(**kwargs):
        # The fake service enforces the version condition, like Blob storage.
        if kwargs.get("etag") != observed.properties.etag:
            raise _failure(412, "ConditionNotMet")
        return observed

    blob.download_blob = AsyncMock(side_effect=conditional_download)
    async with store.open_read("key", expected_version=ObjectVersion('"version"')) as read:
        assert read.info.version == '"version"'
        assert await read.body.read() == b"abc"
    _, kwargs = blob.download_blob.call_args
    assert kwargs["etag"] == '"version"'
    with pytest.raises(StorageError) as rejected:
        async with store.open_read("key", expected_version=ObjectVersion('"old"')):
            pass
    assert rejected.value.kind == "precondition_failed"
    _, kwargs = blob.download_blob.call_args
    assert kwargs["match_condition"] == MatchConditions.IfNotModified


@pytest.mark.asyncio
async def test_open_read_maps_a_missing_versioned_source(store):
    missing = _failure(404, "BlobNotFound")
    blob = store._container.get_blob_client.return_value
    blob.download_blob = AsyncMock(side_effect=missing)
    with pytest.raises(StorageError) as raised:
        async with store.open_read("key", expected_version=ObjectVersion('"old"')):
            pass
    # A missing object under a versioned read is absence, like the S3 adapter.
    assert raised.value.kind == "not_found"


@pytest.mark.asyncio
async def test_open_read_missing_without_version_is_not_found(store):
    blob = store._container.get_blob_client.return_value
    blob.download_blob = AsyncMock(side_effect=_failure(404, "BlobNotFound"))
    with pytest.raises(StorageError) as raised:
        async with store.open_read("key"):
            pass
    assert raised.value.kind == "not_found"


@pytest.mark.asyncio
async def test_body_reads_incrementally_and_close_stops_consumption():
    from src.core.storage.azure import _AzureObjectBody

    seen = []

    async def chunks():
        for chunk in (b"abc", b"def", b"ghi"):
            seen.append(chunk)
            yield chunk

    body = _AzureObjectBody(
        SimpleNamespace(chunks=chunks), size=9,
        content_md5=None,
    )
    assert await body.read(2) == b"ab"
    assert seen == [b"abc"]
    assert await body.read(3) == b"cde"
    await body.close()
    assert await body.read() == b""
    assert seen == [b"abc", b"def"]


@pytest.mark.asyncio
async def test_raw_download_refuses_corruption_and_truncation():
    import hashlib

    from src.core.storage.azure import _AzureObjectBody

    async def chunks():
        yield b"corrupt"

    body = _AzureObjectBody(
        SimpleNamespace(chunks=chunks), size=7,
        content_md5=hashlib.md5(b"healthy", usedforsecurity=False).digest(),
    )
    with pytest.raises(StorageError, match="backend_error"):
        await body.read()
    body = _AzureObjectBody(SimpleNamespace(chunks=chunks), size=8, content_md5=None)
    with pytest.raises(StorageError, match="backend_error"):
        await body.read()


@pytest.mark.asyncio
async def test_put_forwards_both_conditional_write_forms(store):
    from azure.core import MatchConditions

    blob = store._container.get_blob_client.return_value
    blob.upload_blob = AsyncMock(return_value={"etag": '"new"'})
    result = await store.write("key", b"data", options=WriteOptions(expected_version=ObjectVersion('"old"')))
    assert result.version == '"new"'
    assert blob.upload_blob.call_args.kwargs["etag"] == '"old"'
    assert blob.upload_blob.call_args.kwargs["match_condition"] == MatchConditions.IfNotModified
    await store.write("key", b"data", options=WriteOptions(create_only=True))
    assert blob.upload_blob.call_args.kwargs["match_condition"] == MatchConditions.IfMissing


@pytest.mark.asyncio
async def test_put_rejects_conflicting_conditions():
    with pytest.raises(ValueError):
        WriteOptions(create_only=True, expected_version=ObjectVersion('"v"'))


@pytest.mark.asyncio
async def test_delete_is_idempotent_and_conditional(store):
    from azure.core import MatchConditions

    blob_deleted = AsyncMock()
    store._container.delete_blob = blob_deleted
    await store.delete("key")
    await store.delete("gone", expected_version=ObjectVersion('"v"'))
    assert blob_deleted.call_args.kwargs["etag"] == '"v"'
    assert blob_deleted.call_args.kwargs["match_condition"] == MatchConditions.IfNotModified


@pytest.mark.asyncio
async def test_delete_swallows_a_lost_blob(store):
    store._container.delete_blob = AsyncMock(side_effect=_failure(404, "BlobNotFound"))
    await store.delete("key")


@pytest.mark.asyncio
async def test_delete_many_reports_partial_cleanup_failure(store):
    store._container.delete_blob = AsyncMock(side_effect=[None, _failure(403, "denied")])
    with pytest.raises(StorageError) as raised:
        await store.delete_many(["a", "b"])
    assert raised.value.provider_code == "PartialDeleteFailure"


# Streams and copies -----------------------------------------------------------


@pytest.mark.asyncio
async def test_write_stream_uploads_small_objects_directly(store):
    blob = store._container.get_blob_client.return_value
    blob.upload_blob = AsyncMock(return_value={"etag": '"small"'})

    async def chunks():
        yield b"ab"
        yield b"cd"

    result = await store.write_stream("key", chunks())
    assert (result.version, result.size, result.sha256) == (
        '"small"', 4, "88d4266fd4e6338d13b845fcf289579d209c897823b9217da3e161936f031589",
    )
    blob.upload_blob.assert_awaited_once()
    blob.stage_block.assert_not_called()


@pytest.mark.asyncio
async def test_write_stream_stages_large_objects_in_blocks(store):
    from azure.storage.blob import BlockState

    blob = store._container.get_blob_client.return_value
    blob.stage_block = AsyncMock()
    blob.commit_block_list = AsyncMock(return_value={"etag": '"big"'})

    async def chunks():
        yield b"x" * (8 * 1024**2)
        yield b"y"

    result = await store.write_stream("key", chunks())
    assert blob.stage_block.await_count == 2
    assert result.size == 8 * 1024**2 + 1
    blocks = blob.commit_block_list.call_args.args[0]
    assert all(block.state == BlockState.UNCOMMITTED for block in blocks)
    assert result.version == '"big"'


@pytest.mark.asyncio
async def test_copy_reuploads_empty_sources(store):
    blob = store._container.get_blob_client.return_value
    blob.download_blob = AsyncMock(return_value=_stream([b""], _props(size=0, etag='"empty"')))
    blob.upload_blob = AsyncMock(return_value={"etag": '"copy"'})
    from src.core.storage.contracts import ObjectInfo

    source = ObjectInfo(
        key="source", size=0, version=ObjectVersion('"empty"'),
        modified_at=None, properties=ContentProperties(), metadata={}, http_etag='"empty"',
    )
    result = await store.copy(source, "dest")
    assert result.version == '"copy"'
    blob.stage_block_from_url.assert_not_called()


@pytest.mark.asyncio
async def test_copy_stages_block_ranges_with_source_condition(store, monkeypatch):
    from azure.storage.blob import BlockState

    from src.core.storage.contracts import ObjectInfo

    blob = store._container.get_blob_client.return_value
    blob.stage_block_from_url = AsyncMock()
    blob.commit_block_list = AsyncMock(return_value={"etag": '"copy"'})
    monkeypatch.setattr(store, "_read_sas_url", AsyncMock(return_value="https://fixture/source"))
    source = ObjectInfo(
        key="source", size=300 * 1024**2, version=ObjectVersion('"source"'),
        modified_at=None, properties=ContentProperties(content_type="text/plain"),
        metadata={"bifrost-move-row": "row"}, http_etag='"source"',
    )
    result = await store.copy(source, "dest")
    assert result.version == '"copy"'
    calls = blob.stage_block_from_url.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["source_offset"] == 0
    assert calls[1].kwargs["source_offset"] == 256 * 1024**2
    assert all(call.kwargs["headers"] == {"x-ms-source-if-match": '"source"'} for call in calls)
    assert all(block.state == BlockState.UNCOMMITTED for block in blob.commit_block_list.call_args.args[0])


@pytest.mark.asyncio
async def test_copy_falls_back_to_streaming_when_server_copy_is_unimplemented(store, monkeypatch):
    from src.core.storage.contracts import ObjectInfo

    blob = store._container.get_blob_client.return_value
    blob.stage_block_from_url = AsyncMock(side_effect=_failure(501, "APINotImplemented"))
    blob.download_blob = AsyncMock(
        return_value=_stream([b"12345678"], _props(size=8, etag='"source"'))
    )
    blob.upload_blob = AsyncMock(return_value={"etag": '"streamed"'})
    monkeypatch.setattr(store, "_read_sas_url", AsyncMock(return_value="https://fixture/source"))
    source = ObjectInfo(
        key="source", size=8, version=ObjectVersion('"source"'),
        modified_at=None, properties=ContentProperties(), metadata={}, http_etag='"source"',
    )
    result = await store.copy(source, "dest", options=CopyOptions(create_only=True))
    assert result.version == '"streamed"'
    blob.stage_block_from_url.assert_awaited_once()
    blob.upload_blob.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_fallback_reports_a_stale_source(store, monkeypatch):
    from src.core.storage.contracts import CopyConditionFailed, ObjectInfo

    blob = store._container.get_blob_client.return_value
    blob.stage_block_from_url = AsyncMock(side_effect=_failure(501, "APINotImplemented"))
    blob.download_blob = AsyncMock(side_effect=_failure(412, "ConditionNotMet"))
    monkeypatch.setattr(store, "_read_sas_url", AsyncMock(return_value="https://fixture/source"))
    source = ObjectInfo(
        key="source", size=8, version=ObjectVersion('"source"'),
        modified_at=None, properties=ContentProperties(), metadata={}, http_etag='"source"',
    )
    with pytest.raises(CopyConditionFailed) as raised:
        await store.copy(source, "dest")
    assert raised.value.side == "source"


@pytest.mark.asyncio
async def test_copy_reports_destination_collisions_on_every_path(store, monkeypatch):
    from src.core.storage.contracts import CopyConditionFailed, ObjectInfo

    blob = store._container.get_blob_client.return_value
    source = ObjectInfo(
        key="source", size=8, version=ObjectVersion('"source"'),
        modified_at=None, properties=ContentProperties(), metadata={}, http_etag='"source"',
    )
    # Empty-source path: the conditional destination write loses the race.
    from dataclasses import replace

    blob.download_blob = AsyncMock(
        return_value=_stream([b""], _props(size=0, etag='"source"'))
    )
    blob.upload_blob = AsyncMock(side_effect=_failure(409, "BlobAlreadyExists"))
    with pytest.raises(CopyConditionFailed) as raised:
        await store.copy(
            replace(source, size=0), "dest", options=CopyOptions(create_only=True),
        )
    assert raised.value.side == "destination"
    # Streaming fallback path: the conditional single-part upload loses the race.
    blob.download_blob = AsyncMock(
        return_value=_stream([b"12345678"], _props(size=8, etag='"source"'))
    )
    blob.stage_block_from_url = AsyncMock(side_effect=_failure(501, "APINotImplemented"))
    blob.upload_blob = AsyncMock(side_effect=_failure(409, "BlobAlreadyExists"))
    monkeypatch.setattr(store, "_read_sas_url", AsyncMock(return_value="https://fixture/source"))
    with pytest.raises(CopyConditionFailed) as raised:
        await store.copy(source, "dest", options=CopyOptions(create_only=True))
    assert raised.value.side == "destination"


@pytest.mark.asyncio
async def test_create_only_violation_is_a_precondition_failure(store):
    blob = store._container.get_blob_client.return_value
    blob.upload_blob = AsyncMock(side_effect=_failure(409, "BlobAlreadyExists"))
    with pytest.raises(StorageError) as raised:
        await store.write("key", b"duplicate", options=WriteOptions(create_only=True))
    assert raised.value.kind == "precondition_failed"


@pytest.mark.asyncio
async def test_copy_maps_source_and_destination_condition_failures(store, monkeypatch):
    from src.core.storage.contracts import ObjectInfo

    blob = store._container.get_blob_client.return_value
    monkeypatch.setattr(store, "_read_sas_url", AsyncMock(return_value="https://fixture/source"))
    source = ObjectInfo(
        key="source", size=8, version=ObjectVersion('"source"'),
        modified_at=None, properties=ContentProperties(), metadata={}, http_etag='"source"',
    )
    blob.stage_block_from_url = AsyncMock(side_effect=_failure(412, "ConditionNotMet"))
    with pytest.raises(StorageError) as raised:
        await store.copy(source, "dest")
    assert raised.value.kind == "precondition_failed"
    blob.stage_block_from_url = AsyncMock()
    blob.commit_block_list = AsyncMock(side_effect=_failure(412, "ConditionNotMet"))
    with pytest.raises(StorageError) as raised:
        await store.copy(source, "dest")
    assert raised.value.kind == "precondition_failed"


# Listing and signing ----------------------------------------------------------


class _Pager:
    """One canned page with a preset continuation token."""

    def __init__(self, page, token):
        self._page = page
        self.continuation_token = token

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._page is None:
            raise StopAsyncIteration
        page, self._page = self._page, None
        return page


@pytest.mark.asyncio
async def test_list_page_returns_contract_pages(store):
    import azure.storage.blob.aio as blobs

    store._container.list_blobs = MagicMock(return_value=SimpleNamespace(
        by_page=lambda **kwargs: _Pager(
            [SimpleNamespace(name="a", size=1, etag="v1", last_modified=None)], "token-1",
        ),
    ))
    first = await store.list_page("p/", limit=1)
    assert [item.key for item in first.objects] == ["a"]
    assert first.objects[0].version == '"v1"'
    assert first.next_cursor == "token-1"
    prefix_item = MagicMock(spec=blobs.BlobPrefix)
    prefix_item.name = "p/sub/"
    store._container.walk_blobs = MagicMock(return_value=SimpleNamespace(
        by_page=lambda **kwargs: _Pager([prefix_item], None),
    ))
    delimited = await store.list_page("p/", delimiter="/")
    assert delimited.prefixes == ("p/sub/",)
    assert delimited.next_cursor is None


@pytest.mark.asyncio
async def test_list_page_rejects_bad_limits(store):
    with pytest.raises(ValueError):
        await store.list_page(limit=0)


@pytest.mark.asyncio
async def test_signed_upload_requires_the_block_type_header():
    config = settings()
    store = AzureObjectStore("contract", config)
    store._container = MagicMock()
    store._container.get_blob_client.return_value.url = "https://example.blob.core.windows.net/contract/key"
    request = await store.signed_request("key", "PUT", content_type="text/plain")
    assert request.headers["x-ms-blob-type"] == "BlockBlob"
    assert request.headers["Content-Type"] == "text/plain"
    assert request.url.startswith("https://example.blob.core.windows.net/contract/key?")
    assert "sig=" in request.url
    download = await store.signed_request("key", "GET", response_content_type="text/plain")
    assert download.headers == {}
    with pytest.raises(ValueError):
        await store.signed_request("key", "PUT", expires_in=604801)
    with pytest.raises(ValueError):
        await store.signed_request("key", "PUT", response_content_type="text/plain")
    with pytest.raises(ValueError):
        await store.signed_request("key", "GET", content_type="text/plain")


@pytest.mark.asyncio
async def test_delegation_sas_uses_a_bounded_key_window(monkeypatch):
    from azure.storage.blob import UserDelegationKey

    issued = datetime(2030, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return issued

    monkeypatch.setattr("src.core.storage.azure.datetime", Clock)
    config = settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
    store = AzureObjectStore("contract", config)
    key = UserDelegationKey()
    key.signed_oid = "00000000-0000-0000-0000-000000000001"
    key.signed_tid = "00000000-0000-0000-0000-000000000002"
    key.signed_start = "2030-01-01T00:00:00Z"
    key.signed_expiry = "2030-01-08T00:00:00Z"
    key.signed_service = "b"
    key.signed_version = "2025-11-05"
    key.value = LOCAL_KEY
    store._service = SimpleNamespace(get_user_delegation_key=AsyncMock(return_value=key))
    store._container = MagicMock()
    store._container.get_blob_client.return_value.url = (
        "https://example.blob.core.windows.net/contract/signed"
    )
    from urllib.parse import parse_qs, urlsplit

    url = (await store.signed_request("signed", "GET", expires_in=604800)).url
    query = parse_qs(urlsplit(url).query)
    assert query["sp"] == ["r"]
    assert query["se"] == [key.signed_expiry]
    assert query["sig"]
    store._service.get_user_delegation_key.assert_awaited_once_with(
        key_start_time=issued, key_expiry_time=issued + timedelta(days=7),
    )
    with pytest.raises(ValueError, match="604800"):
        await store.signed_request("signed", "GET", expires_in=604801)
    store._service.get_user_delegation_key.assert_awaited_once()


# Lifecycle ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_releases_credential_even_when_service_close_fails(store):
    credential = SimpleNamespace(close=AsyncMock())
    store._credential = credential
    store._service = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close")))
    with pytest.raises(RuntimeError):
        await store.close()
    credential.close.assert_awaited_once()
    assert store._service is None


@pytest.mark.asyncio
async def test_delete_without_retry_replays_nothing_after_a_lost_response():
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.core.exceptions import ServiceResponseError
    from azure.core.pipeline.transport import AsyncHttpTransport
    from azure.storage.blob.aio import BlobServiceClient, ExponentialRetry

    class LostResponse(AsyncHttpTransport):
        attempts = 0

        async def open(self):
            pass

        async def close(self):
            pass

        async def __aexit__(self, *_args):
            await self.close()

        async def send(self, request, **kwargs):
            assert request.method == "DELETE"
            self.attempts += 1
            raise ServiceResponseError("response lost after delete")

    config = settings()
    store = AzureObjectStore("contract", config)
    wire = LostResponse()
    store._service = BlobServiceClient(
        config.azure_blob_account_url,
        credential=AzureNamedKeyCredential("example", LOCAL_KEY),
        transport=wire,
        retry_policy=ExponentialRetry(
            initial_backoff=0, increment_base=0, random_jitter_range=0, retry_total=1
        ),
    )
    store._container = store._service.get_container_client("contract")
    try:
        with pytest.raises(StorageError) as raised:
            await store.delete("key")
        assert raised.value.kind == "unavailable"
        assert wire.attempts == 1
    finally:
        await store.close()


def test_credential_selection_and_closure(monkeypatch):
    import azure.identity
    import azure.storage.blob

    credential = MagicMock()
    identity = MagicMock(return_value=credential)
    service = MagicMock()
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", identity)
    monkeypatch.setattr(
        azure.storage.blob, "BlobServiceClient", MagicMock(return_value=service)
    )
    client = SyncAzureObjectStore(
        "contract", settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
    )
    identity.assert_called_once_with(client_id=None)
    assert client._namespace == "contract"
    client.close()
    service.close.assert_called_once()
    credential.close.assert_called_once()


def test_sync_construction_failure_releases_owned_identity(monkeypatch):
    credential = MagicMock()
    monkeypatch.setattr(
        "azure.identity.ManagedIdentityCredential", MagicMock(return_value=credential)
    )
    monkeypatch.setattr(
        "azure.storage.blob.BlobServiceClient",
        MagicMock(side_effect=RuntimeError("client construction failed")),
    )
    with pytest.raises(RuntimeError, match="client construction failed"):
        SyncAzureObjectStore(
            "contract",
            settings(azure_blob_auth="managed_identity", azure_blob_account_key=None),
        )
    credential.close.assert_called_once()


@pytest.mark.asyncio
async def test_async_identity_selection_and_closure(monkeypatch):
    import azure.identity.aio
    import azure.storage.blob.aio

    credential = SimpleNamespace(close=AsyncMock())
    identity = MagicMock(return_value=credential)
    service = MagicMock()
    service.close = AsyncMock()
    factory = MagicMock(return_value=service)
    monkeypatch.setattr(azure.identity.aio, "ManagedIdentityCredential", identity)
    monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient", factory)
    config = settings(azure_blob_auth="managed_identity", azure_blob_account_key=None)
    async with AzureObjectStore("contract", config) as store:
        identity.assert_called_once_with(client_id=None)
        factory.assert_called_once_with(config.azure_blob_account_url, credential=credential)
        service.get_container_client.assert_called_once_with("contract")
        assert store._container is service.get_container_client.return_value
    service.close.assert_awaited_once()
    credential.close.assert_awaited_once()


def test_sync_read_validates_and_maps_errors():
    client = SyncAzureObjectStore.__new__(SyncAzureObjectStore)
    client._credential = None
    stream = SimpleNamespace(
        properties=_props(size=6),
        readall=MagicMock(return_value=b"source"),
    )
    blob = SimpleNamespace(download_blob=MagicMock(return_value=stream))
    client._client = SimpleNamespace(get_blob_client=MagicMock(return_value=blob))
    client._namespace = "contract"
    assert client.read("key") == b"source"
    blob.download_blob.assert_called_once()
    blob.download_blob.side_effect = _failure(404, "BlobNotFound")
    with pytest.raises(StorageError) as raised:
        client.read("key")
    assert raised.value.kind == "not_found"
