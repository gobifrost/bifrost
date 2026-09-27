import ssl

import pytest

from src.core.database import _prepare_asyncpg_url


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_encrypted_database_modes_verify_certificate_and_hostname(sslmode):
    url, connect_args = _prepare_asyncpg_url(
        f"postgresql://user:pass@db.example.com/app?sslmode={sslmode}&application_name=test"
    )

    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert "sslmode" not in url
    assert "application_name=test" in url


def test_prefer_preserves_asyncpg_negotiation_mode():
    url, connect_args = _prepare_asyncpg_url(
        "postgresql://user:pass@db.example.com/app?sslmode=prefer"
    )

    assert connect_args == {"ssl": "prefer"}
    assert "sslmode" not in url
