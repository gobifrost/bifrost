"""Shared form-runtime constants and capability fingerprint rules."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any, Awaitable, cast
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email

DEFAULT_FORM_CONFIRMATION_MARKDOWN = "## Form submitted\n\nThank you!"
MAX_FORM_CONFIRMATION_MARKDOWN_LENGTH = 20_000
FORM_STARTUP_TTL_SECONDS = 30 * 60


class FormRuntimeValidationError(ValueError):
    """Raised when submitted values do not match the authoritative form schema."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__("Invalid form submission")
        self.errors = errors


def _field_type(field: Any) -> str:
    return getattr(field.type, "value", field.type)


def validate_form_submission(
    form: Any,
    form_data: dict[str, Any],
    *,
    embed_upload_prefix: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize browser values against persisted form fields."""

    input_fields = {
        field.name: field
        for field in getattr(form, "fields", [])
        if _field_type(field) not in {"markdown", "html"}
    }
    errors: list[dict[str, str]] = []
    for name in sorted(set(form_data) - set(input_fields)):
        errors.append({"field": name, "message": "Unknown form field"})

    cleaned: dict[str, Any] = {}
    for name, field in input_fields.items():
        value = form_data.get(name, getattr(field, "default_value", None))
        missing = value is None or value == "" or value == []
        if missing:
            if field.required:
                errors.append({"field": name, "message": "This field is required"})
            continue

        kind = _field_type(field)
        valid = True
        if kind in {
            "text",
            "email",
            "select",
            "radio",
            "textarea",
            "date",
            "datetime",
        }:
            valid = isinstance(value, str)
        elif kind == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif kind == "checkbox":
            valid = isinstance(value, bool)
        elif kind == "multi_select":
            valid = isinstance(value, list) and all(
                isinstance(item, str) for item in value
            )
        elif kind == "file":
            paths = value if isinstance(value, list) else [value]
            valid = (
                all(isinstance(path, str) and bool(path) for path in paths)
                and len(paths) <= 20
                and (bool(getattr(field, "multiple", False)) or len(paths) == 1)
            )
            if valid and embed_upload_prefix is not None:
                valid = all(
                    path.startswith(embed_upload_prefix)
                    and ".." not in path.split("/")
                    for path in paths
                )

        if not valid:
            errors.append({"field": name, "message": f"Invalid {kind} value"})
            continue

        if kind == "email" and isinstance(value, str):
            try:
                validate_email(value, check_deliverability=False)
            except EmailNotValidError:
                errors.append({"field": name, "message": "Invalid email address"})
                continue
        if kind == "date" and isinstance(value, str):
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append({"field": name, "message": "Invalid date value"})
                continue
        if kind == "datetime" and isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append({"field": name, "message": "Invalid datetime value"})
                continue

        options = {
            option.get("value")
            for option in (getattr(field, "options", None) or [])
            if isinstance(option, dict)
        }
        if options and kind in {"select", "radio"} and value not in options:
            errors.append({"field": name, "message": "Invalid option"})
            continue
        if (
            options
            and kind == "multi_select"
            and isinstance(value, list)
            and any(item not in options for item in value)
        ):
            errors.append({"field": name, "message": "Invalid option"})
            continue

        validation = getattr(field, "validation", None) or {}
        if validation.get("pattern") and isinstance(value, str):
            try:
                matches = re.fullmatch(validation["pattern"], value) is not None
            except re.error:
                matches = False
            if not matches:
                errors.append(
                    {
                        "field": name,
                        "message": validation.get("message") or "Invalid format",
                    }
                )
                continue

        measure = value if isinstance(value, (int, float)) else len(value)
        if validation.get("min") is not None and measure < validation["min"]:
            errors.append(
                {"field": name, "message": validation.get("message") or "Value is too small"}
            )
            continue
        if validation.get("max") is not None and measure > validation["max"]:
            errors.append(
                {"field": name, "message": validation.get("message") or "Value is too large"}
            )
            continue

        cleaned[name] = value

    if errors:
        raise FormRuntimeValidationError(errors)
    return cleaned


def _startup_owner(user: Any) -> str:
    if user.embed:
        if not user.jti:
            raise ValueError("Embed session is missing a session identifier")
        return f"embed:{user.jti}"
    return f"user:{user.user_id}"


def _startup_key(handle: str) -> str:
    return f"bifrost:form:startup:{handle}"


async def store_startup_result(
    *,
    form_id: str,
    organization_id: str | None,
    user: Any,
    result: Any,
) -> tuple[str, datetime]:
    """Store authoritative launch output behind a random, session-bound handle."""

    from src.core.cache.redis_client import get_redis

    now = int(datetime.now(timezone.utc).timestamp())
    ttl = FORM_STARTUP_TTL_SECONDS
    if user.token_exp is not None:
        ttl = max(1, min(ttl, user.token_exp - now))
    expires_at = datetime.fromtimestamp(now + ttl, tz=timezone.utc)
    handle = secrets.token_urlsafe(48)
    record = {
        "form_id": form_id,
        "organization_id": organization_id,
        "grant": user.grant if user.embed else "authenticated",
        "owner": _startup_owner(user),
        "result": result,
    }
    async with get_redis() as redis:
        await redis.setex(_startup_key(handle), ttl, json.dumps(record))
    return handle, expires_at


async def load_startup_result(
    *,
    handle: str,
    form_id: str,
    organization_id: str | None,
    user: Any,
) -> Any:
    """Resolve startup state only for the exact form and principal that created it."""

    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        raw = await redis.get(_startup_key(handle))
    if raw is None:
        raise ValueError("Startup handle is invalid or expired")
    record = json.loads(raw)
    expected = {
        "form_id": form_id,
        "organization_id": organization_id,
        "grant": user.grant if user.embed else "authenticated",
        "owner": _startup_owner(user),
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("Startup handle is invalid or expired")
    return record.get("result")


async def consume_startup_result(handle: str) -> None:
    """Delete a form-session startup handle after durable acceptance."""

    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        await redis.delete(_startup_key(handle))


def _submission_key(user: Any) -> str:
    if not user.jti:
        raise ValueError("Embed session is missing a session identifier")
    return f"bifrost:form:submission:{user.jti}"


def _upload_registry_key(user: Any) -> str:
    if not user.jti:
        raise ValueError("Embed session is missing a session identifier")
    return f"bifrost:form:uploads:{user.jti}"


async def register_embed_upload(
    user: Any,
    *,
    path: str,
    field_name: str,
    content_type: str,
    file_size: int,
) -> None:
    """Record one server-minted attachment reference for this form session."""

    from src.core.cache.redis_client import get_redis

    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(1, (user.token_exp or now + FORM_STARTUP_TTL_SECONDS) - now)
    record = json.dumps(
        {
            "field_name": field_name,
            "content_type": content_type,
            "file_size": file_size,
        }
    )
    async with get_redis() as redis:
        key = _upload_registry_key(user)
        await cast(Awaitable[int], redis.hset(key, path, record))
        await redis.expire(key, ttl)


async def validate_embed_upload_references(
    user: Any,
    form: Any,
    form_data: dict[str, Any],
) -> None:
    """Require every submitted file path to have been minted for that field."""

    from src.core.cache.redis_client import get_redis

    file_fields = {
        field.name: field
        for field in getattr(form, "fields", [])
        if _field_type(field) == "file" and field.name in form_data
    }
    async with get_redis() as redis:
        key = _upload_registry_key(user)
        for field_name in file_fields:
            value = form_data[field_name]
            paths = value if isinstance(value, list) else [value]
            for path in paths:
                raw = await cast(Awaitable[str | None], redis.hget(key, path))
                if raw is None or json.loads(raw).get("field_name") != field_name:
                    raise ValueError("Attachment reference is invalid")


async def clear_embed_upload_references(user: Any) -> None:
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        await redis.delete(_upload_registry_key(user))


async def reserve_external_submission(user: Any, nonce: str | None) -> None:
    """Atomically reserve the single accepted submission for a form session."""

    from src.core.cache.redis_client import get_redis

    if nonce is None:
        raise ValueError("A submission nonce is required")
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(1, (user.token_exp or now + FORM_STARTUP_TTL_SECONDS) - now)
    async with get_redis() as redis:
        reserved = await redis.set(
            _submission_key(user),
            json.dumps({"state": "pending", "nonce": nonce}),
            ex=ttl,
            nx=True,
        )
    if not reserved:
        raise ValueError("This form session has already submitted")


async def release_external_submission(user: Any) -> None:
    """Release a failed pre-acceptance reservation so the visitor may retry."""

    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        await redis.delete(_submission_key(user))


async def accept_external_submission(user: Any, nonce: str) -> None:
    """Mark a reserved form session as durably accepted until token expiry."""

    from src.core.cache.redis_client import get_redis

    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(1, (user.token_exp or now + FORM_STARTUP_TTL_SECONDS) - now)
    async with get_redis() as redis:
        await redis.setex(
            _submission_key(user),
            ttl,
            json.dumps({"state": "accepted", "nonce": nonce}),
        )


def form_capability_document(form: Any) -> dict[str, Any]:
    """Return the security-relevant portion of a form's public capability."""

    provider_fields: list[dict[str, Any]] = []
    file_fields: list[dict[str, Any]] = []
    executable_fields: list[dict[str, str]] = []

    for field in sorted(getattr(form, "fields", []), key=lambda item: item.position):
        if field.data_provider_id:
            provider_fields.append(
                {
                    "name": field.name,
                    "provider": str(field.data_provider_id),
                    "inputs": field.data_provider_inputs or {},
                    "auto_fill": field.auto_fill or {},
                }
            )
        if field.type == "file":
            file_fields.append(
                {
                    "name": field.name,
                    "allowed_types": sorted(field.allowed_types or []),
                    "multiple": bool(field.multiple),
                    "max_size_mb": field.max_size_mb,
                }
            )
        if field.type == "html":
            executable_fields.append({"name": field.name, "type": field.type})

    return {
        "submission_workflow": form.workflow_id,
        "startup_workflow": form.launch_workflow_id,
        "provider_fields": provider_fields,
        "file_fields": file_fields,
        "executable_fields": executable_fields,
    }


def form_capability_fingerprint(form: Any) -> str:
    """Hash a form's canonical public capability document."""

    encoded = json.dumps(
        form_capability_document(form),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalize_allowed_origins(origins: list[str]) -> list[str]:
    """Validate and canonicalize exact HTTP(S) browser origins."""

    normalized: set[str] = set()
    for raw_origin in origins:
        if raw_origin != raw_origin.strip() or any(ord(char) < 32 for char in raw_origin):
            raise ValueError(f"Invalid origin: {raw_origin!r}")
        if "*" in raw_origin:
            raise ValueError("Wildcard origins are not supported")

        parsed = urlsplit(raw_origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"Invalid exact origin: {raw_origin!r}")

        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid origin port: {raw_origin!r}") from exc

        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 80 if parsed.scheme == "http" else 443
        port_suffix = f":{port}" if port is not None and port != default_port else ""
        normalized.add(f"{parsed.scheme}://{host}{port_suffix}")

    return sorted(normalized)
