from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import reset_test_blob_storage as reset
from src.config import Settings
from src.core.storage.contracts import (
    ObjectSummary, ObjectVersion, StorageError,
)


def settings(**overrides):
    return Settings(
        _env_file=None,
        azure_blob_account_url="https://fixture.blob.core.windows.net",
        azure_blob_container="disposable-test",
        **{"environment": "testing", "object_storage_provider": "azure_blob", **overrides},
    )


@pytest.mark.parametrize("overrides,approved", [
    ({}, False),
    ({"environment": "production"}, True),
    ({"object_storage_provider": "s3"}, True),
])
async def test_reset_requires_explicit_disposable_testing_context(monkeypatch, overrides, approved):
    factory = MagicMock()
    monkeypatch.setattr(reset, "AzureObjectStore", factory)
    with pytest.raises(ValueError, match="explicit disposable-container approval"):
        await reset.reset_blob_test_storage(settings(**overrides), approved=approved)
    factory.from_settings.assert_not_called()


class Fixtures:
    def __init__(self, *, fail_delete=False, retain=False):
        self.remaining = {"_repo/old.txt", "_workspace_sync_checkpoints/local-state.json"}
        self.fail_delete, self.retain = fail_delete, retain
        self.deleted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def list_page(self, prefix="", **kwargs):
        return SimpleNamespace(
            objects=tuple(
                ObjectSummary(key=key, size=1, version=ObjectVersion('"v"'), modified_at=None)
                for key in sorted(self.remaining)
            ),
            prefixes=(), next_cursor=None,
        )

    async def delete_many(self, keys):
        self.deleted.extend(keys)
        if self.fail_delete:
            raise StorageError("backend_error", provider_code="PartialDeleteFailure")
        if not self.retain:
            self.remaining.difference_update(keys)


async def test_reset_clears_published_and_private_git_state(monkeypatch):
    backing = Fixtures()
    factory = MagicMock()
    factory.from_settings.return_value = backing
    monkeypatch.setattr(reset, "AzureObjectStore", factory)
    await reset.reset_blob_test_storage(settings(), approved=True)
    assert backing.remaining == set()
    assert set(backing.deleted) == {"_repo/old.txt", "_workspace_sync_checkpoints/local-state.json"}


@pytest.mark.parametrize("options", [{"fail_delete": True}, {"retain": True}])
async def test_reset_does_not_report_success_when_objects_remain(monkeypatch, options):
    backing = Fixtures(**options)
    factory = MagicMock()
    factory.from_settings.return_value = backing
    monkeypatch.setattr(reset, "AzureObjectStore", factory)
    with pytest.raises((RuntimeError, StorageError)):
        await reset.reset_blob_test_storage(settings(), approved=True)
    assert backing.remaining
