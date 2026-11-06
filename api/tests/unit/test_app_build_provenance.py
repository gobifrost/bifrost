"""Stored build provenance is useful only with a complete immutable dist."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.core.storage.contracts import StorageError
from types import SimpleNamespace
from src.services.solutions.app_build import SolutionAppBuilder
from bifrost.app_build_identity import build_identity, sdk_identity
from tests.unit.test_app_build_identity import RECIPE, _sdk


class Body:
    def __init__(self, content):
        self.content = content

    async def read(self):
        return self.content


@pytest.fixture
def storage(monkeypatch):
    objects = {}
    builder = SolutionAppBuilder()

    class Store:
        async def write(self, key, content):
            objects[key] = content

        @asynccontextmanager
        async def open_read(self, key):
            if key not in objects:
                raise StorageError("not_found", provider_code="fixture")
            yield SimpleNamespace(body=Body(objects[key]))

    @asynccontextmanager
    async def store():
        yield Store()

    monkeypatch.setattr(builder, "_get_store", store)
    return builder, objects


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "missing",
        "changed",
        "unknown",
        "wrong_app",
        "wrong_pointer",
        "malformed",
    ],
)
async def test_only_complete_matching_builds_are_reusable(storage, damage):
    builder, objects = storage
    app, deployment = uuid4(), uuid4()
    dist = {"index.html": b"html", "logo.bin": b"\xff\x00"}
    for path, content in dist.items():
        objects[builder._dist_key(app, path, deployment_id=deployment)] = content
    await builder.write_build_identity(app, deployment, "inputs", dist)
    key = builder._provenance_key(app, deployment)
    record = json.loads(objects[key])
    if damage == "missing":
        del objects[builder._dist_key(app, "logo.bin", deployment_id=deployment)]
    elif damage == "changed":
        objects[builder._dist_key(app, "logo.bin", deployment_id=deployment)] = b"wrong"
    elif damage == "unknown":
        record["schema"] = 2
    elif damage == "wrong_app":
        record["app_id"] = str(uuid4())
    elif damage == "wrong_pointer":
        record["deployment_id"] = str(uuid4())
    if damage == "malformed":
        objects[key] = b"invalid json"
    else:
        objects[key] = json.dumps(record).encode()
    assert await builder.can_reuse(app, deployment, "inputs") is (damage == "none")
    assert not await builder.can_reuse(app, deployment, "different-inputs")


async def test_unknown_deployment_and_missing_provenance_are_misses(storage):
    builder, _ = storage
    assert not await builder.can_reuse(uuid4(), None, "inputs")
    assert not await builder.can_reuse(uuid4(), uuid4(), "inputs")


def test_cli_attestation_is_checked_against_exact_current_sdk(monkeypatch):
    sdk = _sdk([("package/index.js", b"sdk")], 1)
    recipe = {**RECIPE, "sdk": sdk_identity(sdk)}
    monkeypatch.setattr(
        "src.services.sdk_package.build_sdk_tarball", lambda version: sdk
    )
    app = uuid4()
    assert SolutionAppBuilder.attested_identity(
        app, {"index.html": b"x"}, {}, recipe
    ) == build_identity(app, {"index.html": b"x"}, {}, recipe)
    assert (
        SolutionAppBuilder.attested_identity(app, {}, {}, {**recipe, "sdk": "0" * 64})
        is None
    )
    assert SolutionAppBuilder.attested_identity(app, {}, {}, {"schema": 2}) is None
