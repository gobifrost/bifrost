"""Tests for the process-local aiobotocore session factory."""

from unittest.mock import Mock

from src.core import s3_session


def test_get_s3_session_reuses_only_the_current_process(monkeypatch) -> None:
    created = [Mock(name="first"), Mock(name="second")]
    monkeypatch.setattr(s3_session, "_session", None)
    monkeypatch.setattr(s3_session, "_session_pid", None)
    monkeypatch.setattr(s3_session, "get_session", Mock(side_effect=created))
    monkeypatch.setattr(s3_session.os, "getpid", Mock(side_effect=[101, 101, 202]))

    assert s3_session.get_s3_session() is created[0]
    assert s3_session.get_s3_session() is created[0]
    assert s3_session.get_s3_session() is created[1]
    assert s3_session.get_session.call_count == 2
