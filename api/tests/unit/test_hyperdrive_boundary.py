"""Provider-agnostic Hyperdrive boundary tests."""

from pathlib import Path

import pytest

from shared.hyperdrive import (
    HYPERDRIVE_UNSUPPORTED_POSTGRES_FEATURES,
    PROBE_READONLY_QUERY,
    is_direct_postgres_url,
    validate_probe_target,
)


def test_unsupported_features_cover_canonical_coordination() -> None:
    assert "pg_advisory_lock" in HYPERDRIVE_UNSUPPORTED_POSTGRES_FEATURES
    assert "pg_try_advisory_lock" in HYPERDRIVE_UNSUPPORTED_POSTGRES_FEATURES
    assert "LISTEN" in HYPERDRIVE_UNSUPPORTED_POSTGRES_FEATURES
    assert "NOTIFY" in HYPERDRIVE_UNSUPPORTED_POSTGRES_FEATURES


def test_probe_query_is_constant_read_only() -> None:
    assert PROBE_READONLY_QUERY.startswith("SELECT 1 AS ok")
    assert "transaction_read_only" in PROBE_READONLY_QUERY
    assert "current_database()" in PROBE_READONLY_QUERY
    assert "current_user" in PROBE_READONLY_QUERY
    assert "session_user" in PROBE_READONLY_QUERY
    lowered = PROBE_READONLY_QUERY.lower()
    assert "azure" not in lowered
    assert "neon" not in lowered
    assert "amazonaws" not in lowered


def test_probe_target_accepts_generic_provider_names() -> None:
    validate_probe_target(database="qualification_test", role="qualification_reader")
    validate_probe_target(database="my-db_1", role="reader_2")
    validate_probe_target(database="Bifrost", role="App_Reader-1")


@pytest.mark.parametrize(
    "database,role",
    [
        ("", "reader"),
        ("reader", ""),
        ("private user:secret@host", "reader"),
        ("db/name", "reader"),
        ("db name", "reader"),
        ("a" * 64, "reader"),
        ("reader", "role; DROP"),
    ],
)
def test_probe_target_rejects_invalid_names(database: str, role: str) -> None:
    with pytest.raises(ValueError, match="Explicit valid"):
        validate_probe_target(database=database, role=role)


def test_direct_postgres_urls_accepted_for_canonical_pools() -> None:
    assert is_direct_postgres_url("postgresql://user:pass@localhost:5432/bifrost")
    assert is_direct_postgres_url(
        "postgresql+asyncpg://user:pass@localhost:5432/bifrost"
    )
    assert is_direct_postgres_url(
        "postgresql+psycopg://user:pass@localhost:5432/bifrost"
    )


def test_non_direct_urls_rejected_for_canonical_pools() -> None:
    assert not is_direct_postgres_url("")
    assert not is_direct_postgres_url("sqlite:///local.db")
    assert not is_direct_postgres_url("https://example.test/check")


def test_hyperdrive_doc_is_provider_agnostic() -> None:
    doc = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "architecture"
        / "hyperdrive-postgres.md"
    ).read_text(encoding="utf-8")
    lowered = doc.lower()
    # Provider names as examples are allowed; hardcoded provider hostnames,
    # private-link suffixes, and environment snapshots are not.
    for hardcoded in (
        "postgres.database.azure.com",
        "privatelink.",
        "amazonaws.com",
        ".neon.tech",
        "be4bc108214f",
        "10.30.0.",
        "a1d63b24-",
    ):
        assert hardcoded not in lowered, (
            f"hardcoded origin in Hyperdrive doc: {hardcoded}"
        )
    assert "advisory lock" in lowered
    assert "listen" in lowered
