"""Build identities invalidate each independently meaningful input."""

import io
import tarfile
from uuid import uuid4

import pytest

from bifrost.app_build_identity import build_identity, sdk_identity

RECIPE = {
    "schema": 1,
    "variant": "cli-install-v1",
    "sdk": "a" * 64,
    "toolchain": "b" * 64,
    "environment": "c" * 64,
}


def test_identity_is_order_independent_and_binary_sensitive():
    app = uuid4()
    files = {"src/main.tsx": b"hello", "public/logo.png": b"\xff\x00"}
    first = build_identity(app, files, {"react": "18"}, RECIPE)
    assert first == build_identity(
        app, dict(reversed(list(files.items()))), {"react": "18"}, RECIPE
    )
    assert first != build_identity(
        app, {**files, "public/logo.png": b"\xff\x01"}, {"react": "18"}, RECIPE
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("sdk", "d" * 64),
        ("toolchain", "d" * 64),
        ("environment", "d" * 64),
        ("variant", "server-install-v1"),
    ],
)
def test_recipe_change_invalidates(field, value):
    app = uuid4()
    assert build_identity(app, {}, {}, RECIPE) != build_identity(
        app, {}, {}, {**RECIPE, field: value}
    )


def test_identity_covers_app_dependencies_lock_and_default_package():
    app = uuid4()
    first = build_identity(app, {}, {}, RECIPE)
    assert first != build_identity(uuid4(), {}, {}, RECIPE)
    assert first != build_identity(app, {}, {"react": "19"}, RECIPE)
    assert first != build_identity(app, {"package-lock.json": b"{}"}, {}, RECIPE)
    assert first != build_identity(app, {}, {}, RECIPE, package_name="changed")


def _sdk(files, mtime):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files:
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            entry.mtime = mtime
            archive.addfile(entry, io.BytesIO(content))
    return output.getvalue()


def test_sdk_identity_hashes_files_including_types_without_archive_timestamp():
    files = [("package/index.js", b"code"), ("package/index.d.ts", b"types")]
    assert sdk_identity(_sdk(files, 1)) == sdk_identity(_sdk(list(reversed(files)), 2))
    assert sdk_identity(_sdk(files, 1)) != sdk_identity(_sdk(files[:1], 1))


@pytest.mark.parametrize("path", ["../escape", "/absolute", "src/../x"])
def test_unsafe_paths_cannot_be_fingerprinted(path):
    with pytest.raises(ValueError, match="path"):
        build_identity(uuid4(), {path: b"x"}, {}, RECIPE)


def test_build_recipe_covers_environment_and_effective_npm_config_without_values(
    monkeypatch,
):
    from types import SimpleNamespace
    from bifrost.app_build_identity import build_recipe

    config = b'{"registry":"https://registry.example"}'

    def run(argv, **kwargs):
        return SimpleNamespace(stdout=config if "config" in argv else "v1\n")

    monkeypatch.setattr("bifrost.app_build_identity.subprocess.run", run)
    sdk = _sdk([("package/index.js", b"code")], 1)
    first = build_recipe(
        sdk, variant="cli-install-v1", environment={"VITE_LABEL": "one"}
    )
    second = build_recipe(
        sdk, variant="cli-install-v1", environment={"VITE_LABEL": "two"}
    )
    assert first != second
    assert "one" not in str(first)
    config = b'{"registry":"https://other.example"}'
    assert first != build_recipe(
        sdk, variant="cli-install-v1", environment={"VITE_LABEL": "one"}
    )


def test_unknown_recipe_is_rejected_without_type_error():
    with pytest.raises(ValueError, match="recipe"):
        build_identity(uuid4(), {}, {}, {**RECIPE, "variant": []})
