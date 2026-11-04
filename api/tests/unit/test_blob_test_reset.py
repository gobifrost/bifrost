from unittest.mock import MagicMock

import pytest

from scripts import reset_test_blob_storage as reset
from src.config import Settings


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
    monkeypatch.setattr(reset, "AzureBlobTransport", factory)
    with pytest.raises(ValueError, match="explicit disposable-container approval"):
        await reset.reset_blob_test_storage(settings(**overrides), approved=approved)
    factory.assert_not_called()


class Fixtures:
    def __init__(self, *, delete_error=False, retain=False):
        self.pages = [
            {"Contents": [{"Key": "_repo/old.txt"}]},
            {"Contents": [{"Key": "_workspace_sync_checkpoints/local-state.json"}]},
        ]
        self.remaining = {item["Key"] for page in self.pages for item in page["Contents"]}
        self.buckets = []
        self.delete_error, self.retain = delete_error, retain

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    async def paginate(self, *, Bucket, Prefix):
        self.buckets.append(Bucket)
        assert Prefix == ""
        for page in self.pages:
            yield page

    async def delete_objects(self, *, Bucket, Delete):
        self.buckets.append(Bucket)
        if self.delete_error:
            return {"Errors": [{"Code": "AccessDenied"}]}
        if not self.retain:
            self.remaining.difference_update(item["Key"] for item in Delete["Objects"])
        return {}

    async def list_objects_v2(self, *, Bucket):
        self.buckets.append(Bucket)
        return {"Contents": [{"Key": key} for key in self.remaining]}


async def test_reset_clears_published_and_private_git_state_across_pages(monkeypatch):
    client = Fixtures()
    monkeypatch.setattr(reset, "AzureBlobTransport", lambda _settings: client)
    await reset.reset_blob_test_storage(settings(), approved=True)
    assert client.remaining == set()
    assert set(client.buckets) == {"disposable-test"}


@pytest.mark.parametrize("options", [{"delete_error": True}, {"retain": True}])
async def test_reset_does_not_report_success_when_objects_remain(monkeypatch, options):
    client = Fixtures(**options)
    monkeypatch.setattr(reset, "AzureBlobTransport", lambda _settings: client)
    with pytest.raises(RuntimeError):
        await reset.reset_blob_test_storage(settings(), approved=True)
    assert client.remaining
