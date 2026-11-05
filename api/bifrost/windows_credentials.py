"""Transactional, OS-protected Windows credential snapshots.

Only small ASCII fragments reach WinCred (UTF-16, maximum 2560 bytes).
A journal makes interrupted writes and cleanup recoverable. The lock contains
no credentials; every reader and writer holds it while using a snapshot.
"""

import base64
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class CredentialStorageError(RuntimeError):
    """Safe storage-stage diagnostic, without native exception or token text."""


class WindowsCredentialStore:
    SERVICE = "bifrost.credentials.v2"
    LEGACY_SERVICE = "bifrost"
    # Base64 is ASCII: 1200 characters occupy 2400 UTF-16 bytes.
    FRAGMENT_SIZE = 1200

    def __init__(self, keyring, directory: Path):
        self._kr = keyring
        self._directory = directory

    @contextmanager
    def _locked(self):
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            lock = (self._directory / "credentials.lock").open("a+b")
        except OSError:
            raise CredentialStorageError("Credential storage failed at lock creation.") from None
        with lock:
            if os.name == "nt":
                import msvcrt

                # msvcrt locks from the current position, including past EOF.
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                except OSError:
                    raise CredentialStorageError("Credential storage is busy; retry the command.") from None
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read(self, service, username, stage):
        try:
            return self._kr.get_password(service, username)
        except Exception:
            raise CredentialStorageError(f"Credential storage failed at {stage}; retry the command.") from None

    def _write(self, service, username, value, stage):
        try:
            self._kr.set_password(service, username, value)
        except Exception:
            raise CredentialStorageError(f"Credential storage failed at {stage}; retry the command.") from None

    def _delete(self, service, username):
        if self._read(service, username, "cleanup read") is None:
            return
        try:
            self._kr.delete_password(service, username)
        except Exception:
            raise CredentialStorageError("Credential storage failed at cleanup; retry the command.") from None

    @staticmethod
    def _manifest(value):
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or set(value) != {"generation", "count", "sha256"}
            or not isinstance(value["generation"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["generation"])
            or type(value["count"]) is not int
            or value["count"] < 1
            or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
        ):
            raise CredentialStorageError("Credential storage failed at manifest validation.")
        return value

    def _metadata(self, username):
        raw = self._read(self.SERVICE, username, "manifest read")
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError):
            raise CredentialStorageError("Credential storage failed at manifest decoding.") from None

    def _fragment_service(self, manifest, index):
        return f"{self.SERVICE}.{manifest['generation']}.{index}"

    def _remove_generation(self, manifest):
        if manifest is not None:
            for index in range(manifest["count"]):
                self._delete(self._fragment_service(manifest, index), "fragment")

    def _recover(self):
        current = self._manifest(self._metadata("current"))
        journal = self._metadata("pending")
        if journal is not None:
            if not isinstance(journal, dict) or set(journal) != {"previous", "next"}:
                raise CredentialStorageError("Credential storage failed at journal validation.")
            previous = self._manifest(journal["previous"])
            staged = self._manifest(journal["next"])
            if staged is None or staged == previous or current not in (previous, staged):
                raise CredentialStorageError("Credential storage failed at journal validation.")
            if current == staged:
                self._load(current)
            self._remove_generation(previous if current == staged else staged)
            self._delete(self.SERVICE, "pending")
        return current

    def _load(self, manifest):
        if manifest is None:
            return {}
        parts = [
            self._read(self._fragment_service(manifest, index), "fragment", "snapshot read")
            for index in range(manifest["count"])
        ]
        if any(part is None for part in parts):
            raise CredentialStorageError("Credential storage failed at snapshot completeness check.")
        try:
            raw = base64.b64decode("".join(parts), validate=True)
            if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
                raise ValueError
            records = json.loads(raw)
            fields = {"api_url", "access_token", "refresh_token", "expires_at"}
            if not isinstance(records, dict) or any(
                not isinstance(url, str) or (
                    record is not None and (
                        not isinstance(record, dict) or set(record) != fields
                        or any(not isinstance(value, str) for value in record.values())
                        or record["api_url"] != url
                    )
                ) for url, record in records.items()
            ):
                raise ValueError
            return records
        except (ValueError, TypeError):
            raise CredentialStorageError("Credential storage failed at snapshot verification.") from None

    def _clean_legacy(self, records):
        # Null records are logout tombstones: they prevent revival if a legacy
        # deletion is interrupted, and let a later command finish cleanup.
        for url in records:
            self._delete(self.LEGACY_SERVICE, url)

    def _legacy(self, url):
        raw = self._read(self.LEGACY_SERVICE, url, "legacy read")
        if raw is None:
            return None
        try:
            record = json.loads(raw)
            if not isinstance(record, dict) or record.get("api_url", "").rstrip("/") != url:
                return None
            if not all(isinstance(record.get(key), str) for key in (
                "api_url", "access_token", "refresh_token", "expires_at"
            )):
                return None
            return record
        except (ValueError, TypeError):
            return None

    def get(self, url):
        with self._locked():
            records = self._load(self._recover())
            self._clean_legacy(records)
            return records[url] if url in records else self._legacy(url)

    def _commit(self, records, previous):
        raw = json.dumps(records, ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        parts = [encoded[i:i + self.FRAGMENT_SIZE] for i in range(0, len(encoded), self.FRAGMENT_SIZE)]
        staged = {"generation": uuid4().hex, "count": len(parts), "sha256": hashlib.sha256(raw).hexdigest()}
        self._write(self.SERVICE, "pending", json.dumps({"previous": previous, "next": staged}), "journal write")
        for index, part in enumerate(parts):
            self._write(self._fragment_service(staged, index), "fragment", part, "fragment write")
        if self._load(staged) != records:
            raise CredentialStorageError("Credential storage failed at snapshot readback.")
        self._write(self.SERVICE, "current", json.dumps(staged), "activation")
        if self._manifest(self._metadata("current")) != staged:
            raise CredentialStorageError("Credential storage failed at activation readback.")
        self._clean_legacy(records)
        self._recover()

    def save(self, record):
        with self._locked():
            previous = self._recover()
            records = self._load(previous)
            records[record["api_url"]] = record
            self._commit(records, previous)

    def clear(self, url):
        with self._locked():
            previous = self._recover()
            records = self._load(previous)
            records[url] = None
            self._commit(records, previous)

    def list_urls(self):
        with self._locked():
            records = self._load(self._recover())
            self._clean_legacy(records)
            raw = self._read(self.LEGACY_SERVICE, "__index__", "legacy index read")
            try:
                legacy_urls = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                legacy_urls = []
            if not isinstance(legacy_urls, list):
                legacy_urls = []
            urls = {url for url in legacy_urls if isinstance(url, str) and url not in records and self._legacy(url)}
            urls.update(url for url, record in records.items() if record is not None)
            return sorted(urls)
