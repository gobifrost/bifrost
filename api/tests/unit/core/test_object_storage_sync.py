"""The synchronous reader uses the same provider semantics as async IO."""
import httpx
import pytest

from src.core.s3_transport import SyncS3Transport
from src.core.storage.contracts import StorageError
from src.core.storage.s3 import SyncS3ObjectStore


def store_for(client):
    return SyncS3ObjectStore("bucket", SyncS3Transport(
        endpoint_url="https://storage.example", access_key_id="fixture", secret_access_key="fixture",
        region="us-east-1", client=client,
    ))


def test_sync_reader_signs_scoped_key_returns_exact_bytes_and_closes_response():
    responses = []
    def handle(request):
        assert request.url.path == "/bucket/_solutions/install/modules/example.py"
        assert "AWS4-HMAC-SHA256" in request.headers["authorization"]
        response = httpx.Response(200, content=b"\x00\xfffixture")
        responses.append(response)
        return response
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        store = store_for(client)
        assert store.read("_solutions/install/modules/example.py") == b"\x00\xfffixture"
        assert responses[0].is_closed
        store.close()
        assert client.is_closed


@pytest.mark.parametrize("code,status,kind", [
    ("NoSuchKey", 404, "not_found"),
    ("NoSuchBucket", 404, "namespace_not_found"),
    ("AccessDenied", 403, "permission_denied"),
])
def test_sync_reader_maps_confirmed_service_errors(code, status, kind):
    def handle(request):
        return httpx.Response(status, content=f"<Error><Code>{code}</Code></Error>".encode())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(StorageError) as caught:
            store_for(client).read("key")
    assert caught.value.kind == kind


def test_parent_reader_factory_is_cached_and_reset_closes_selected_provider(monkeypatch):
    from unittest.mock import Mock
    from src.core import module_cache_sync as modules
    from src.core import storage

    modules.reset_storage_client()
    selected = Mock()
    factory = Mock(return_value=selected)
    # Health's legacy explicit-key check must not disable the existing S3
    # ambient credential chain used by parent imports.
    monkeypatch.setattr(storage, "storage_configured", lambda settings: False)
    monkeypatch.setattr(storage, "sync_object_store", factory)
    try:
        assert modules._get_storage_client() is selected
        assert modules._get_storage_client() is selected
        factory.assert_called_once()
    finally:
        modules.reset_storage_client()
    selected.close.assert_called_once()


def test_parent_reader_negative_cache_is_cleared_only_by_reset(monkeypatch):
    from unittest.mock import Mock
    from src.core import module_cache_sync as modules
    from src.core import storage

    modules.reset_storage_client()
    selected = Mock()
    factory = Mock(side_effect=[RuntimeError("fixture construction failure"), selected])
    monkeypatch.setattr(storage, "sync_object_store", factory)
    try:
        assert modules._get_storage_client() is None
        assert modules._get_storage_client() is None
        assert factory.call_count == 1
        modules.reset_storage_client()
        assert modules._get_storage_client() is selected
        assert factory.call_count == 2
    finally:
        modules.reset_storage_client()
    selected.close.assert_called_once()
