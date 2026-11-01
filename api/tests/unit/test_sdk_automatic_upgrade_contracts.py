"""Source-compatible automatic transitions are explicit release metadata."""

import json

import pytest

from src.services import sdk_package


@pytest.mark.parametrize("predecessors", [[3], [4], [0], [-1], [True], ["2"], "2"])
def test_invalid_automatic_upgrade_declaration_is_rejected(tmp_path, monkeypatch, predecessors):
    (tmp_path / "sdk-contract.json").write_text(json.dumps({
        "version": 3, "automatic_upgrade_from": predecessors,
    }))
    monkeypatch.setattr(sdk_package, "_SDK_SRC", tmp_path)
    sdk_package.sdk_automatic_upgrade_contracts.cache_clear()
    try:
        with pytest.raises(ValueError, match="automatic-upgrade"):
            sdk_package.sdk_automatic_upgrade_contracts()
    finally:
        sdk_package.sdk_automatic_upgrade_contracts.cache_clear()


def test_only_listed_source_contracts_can_upgrade_automatically(tmp_path, monkeypatch):
    (tmp_path / "sdk-contract.json").write_text(json.dumps({
        "version": 3, "automatic_upgrade_from": [2],
    }))
    monkeypatch.setattr(sdk_package, "_SDK_SRC", tmp_path)
    sdk_package.sdk_automatic_upgrade_contracts.cache_clear()
    try:
        assert sdk_package.sdk_automatic_upgrade_contracts() == frozenset({2})
    finally:
        sdk_package.sdk_automatic_upgrade_contracts.cache_clear()


def test_undeclared_transition_never_opts_in_automatically(tmp_path, monkeypatch):
    (tmp_path / "sdk-contract.json").write_text(json.dumps({"version": 3}))
    monkeypatch.setattr(sdk_package, "_SDK_SRC", tmp_path)
    sdk_package.sdk_automatic_upgrade_contracts.cache_clear()
    try:
        assert sdk_package.sdk_automatic_upgrade_contracts() == frozenset()
    finally:
        sdk_package.sdk_automatic_upgrade_contracts.cache_clear()


def test_hosted_migration_accepts_the_current_wire_contract(tmp_path, monkeypatch):
    (tmp_path / "sdk-contract.json").write_text(json.dumps({
        "version": 3, "automatic_hosted_migration_from": [3],
    }))
    monkeypatch.setattr(sdk_package, "_SDK_SRC", tmp_path)
    sdk_package.sdk_automatic_hosted_migration_contracts.cache_clear()
    try:
        assert sdk_package.sdk_automatic_hosted_migration_contracts() == frozenset({3})
    finally:
        sdk_package.sdk_automatic_hosted_migration_contracts.cache_clear()


def test_hosted_migration_rejects_a_future_wire_contract(tmp_path, monkeypatch):
    (tmp_path / "sdk-contract.json").write_text(json.dumps({
        "version": 3, "automatic_hosted_migration_from": [4],
    }))
    monkeypatch.setattr(sdk_package, "_SDK_SRC", tmp_path)
    sdk_package.sdk_automatic_hosted_migration_contracts.cache_clear()
    try:
        with pytest.raises(ValueError, match="hosted SDK migration"):
            sdk_package.sdk_automatic_hosted_migration_contracts()
    finally:
        sdk_package.sdk_automatic_hosted_migration_contracts.cache_clear()
