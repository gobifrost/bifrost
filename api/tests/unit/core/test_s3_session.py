"""Tests for the process-local CRT transport factory."""

from unittest.mock import Mock

from src.core import s3_session


def test_get_s3_session_reuses_only_the_current_process(monkeypatch) -> None:
    created = [Mock(name="first"), Mock(name="second")]
    monkeypatch.setattr(s3_session, "_session", None)
    monkeypatch.setattr(s3_session, "S3TransportFactory", Mock(side_effect=created))
    monkeypatch.setattr(s3_session.os, "getpid", Mock(side_effect=[101, 101, 202]))

    assert s3_session.get_s3_session() is created[0]
    assert s3_session.get_s3_session() is created[0]
    assert s3_session.get_s3_session() is created[1]
    assert s3_session.S3TransportFactory.call_count == 2


def test_factory_owns_no_client_and_passes_only_s3_configuration(monkeypatch) -> None:
    transport = Mock()
    monkeypatch.setattr(s3_session, "S3Transport", Mock(return_value=transport))

    created = s3_session.S3TransportFactory().create_client(
        "s3",
        endpoint_url="http://storage.test",
        aws_access_key_id="id",
        aws_secret_access_key="secret",
        region_name="us-east-1",
    )

    assert created is transport
    s3_session.S3Transport.assert_called_once_with(
        endpoint_url="http://storage.test",
        access_key_id="id",
        secret_access_key="secret",
        session_token=None,
        region="us-east-1",
    )
