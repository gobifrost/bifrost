# Per-Mapping OAuth Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow per-organization OAuth Connect on integration mappings — each mapping can carry its own access/refresh token and resolved `entity_id`, while the integration-level connection becomes an optional fallback for orgs not individually connected.

**Architecture:** Three coordinated changes: (1) `OAuthToken` gains per-token status fields; `OAuthProvider` gains a declarative `entity_id_source` config column. (2) OAuth `authorize` / `callback` endpoints accept a `mapping_id` carried through `state`; on success the new `OAuthToken` is linked to `IntegrationMapping.oauth_token_id` and `entity_id` is captured per the provider's `entity_id_source`. (3) Workflow-runtime token lookup prefers the mapping's own token over the integration-level fallback; the refresh scheduler writes status per-token instead of per-provider. UI exposes per-row Connect + status badges and stops hiding the mapping table when no data provider is configured.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic on the API; React + TypeScript + Vite on the client; PostgreSQL. Existing test runners: `./test.sh` (backend, Docker stack) and vitest/playwright (client). Hot reload via `./debug.sh`.

**Design doc:** `~/Sync/Obsidian/Projects/Bifrost/Design/2026-05-16 Per-Mapping OAuth Connections.md`

---

## Reading List (skim before starting)

- `api/src/models/orm/oauth.py` — `OAuthProvider`, `OAuthToken` ORM
- `api/src/models/orm/integrations.py` — `IntegrationMapping` ORM
- `api/src/services/oauth_provider.py` — `build_token_refresh_context`, `get_url_resolution_defaults`, `resolve_url_template`, `refresh_oauth_token_http`
- `api/src/routers/oauth_connections.py` — `authorize_connection` (line 547), `oauth_callback` (line 776), `OAuthConnectionRepository.store_token` (line 278), `get_credentials` (line 923)
- `api/src/jobs/schedulers/oauth_token_refresh.py` — refresh sweep
- `api/src/routers/integrations.py` — mapping CRUD (line 259 onward), mapping list endpoint (line 1023), `IntegrationMappingResponse` shape
- `client/src/components/integrations/IntegrationMappingsTab.tsx` — per-row UI (the `hasDataProvider` empty-state guard at line 142 is removed in this plan)
- `client/src/components/integrations/IntegrationOverview.tsx` — integration-level Connect button (relabeled in this plan)

---

## File Structure

### Backend — new files
- `api/alembic/versions/<timestamp>_add_per_token_status_and_entity_id_source.py` — migration: 3 columns on `oauth_tokens` (`status`, `status_message`, `last_refresh_at`), 1 column on `oauth_providers` (`entity_id_source` JSONB)
- `api/src/services/oauth_entity_id.py` — pure helper that reads `entity_id_source` config + callback artifacts (URL params, token response dict, decoded id_token claims) and returns the captured `entity_id` (or None)
- `api/src/services/oauth_state.py` — signed state token helpers (`encode_state(payload) -> str`, `decode_state(token) -> dict`); payload includes a `nonce`, optional `mapping_id`, expiry timestamp. Uses HMAC with `OAUTH_STATE_SECRET` env var (added in this plan)
- `api/tests/unit/test_oauth_state.py` — round-trip + tamper + expiry tests
- `api/tests/unit/test_oauth_entity_id.py` — extraction for each source type
- `api/tests/unit/test_oauth_per_mapping_callback.py` — callback wires the token to the mapping and writes entity_id
- `api/tests/e2e/oauth/test_per_mapping_connect.py` — end-to-end auth-code flow for a mapping

### Backend — modified files
- `api/src/models/orm/oauth.py` — add status fields + entity_id_source
- `api/src/models/contracts/oauth.py` — extend response models with per-token status; add `entity_id_source` to provider create/update
- `api/src/models/contracts/integrations.py` — extend `IntegrationMappingResponse` with `connection_status`, `connection_message`, `last_refresh_at`
- `api/src/routers/oauth_connections.py` — `authorize_connection` accepts optional `mapping_id`; callback decodes `state`, resolves `mapping_id`, captures `entity_id`, links token
- `api/src/routers/integrations.py` — new endpoint `POST /api/integrations/{id}/mappings/{mapping_id}/oauth/authorize`, new endpoint `POST /api/integrations/{id}/mappings/{mapping_id}/oauth/disconnect`; mapping list response includes per-token status
- `api/src/services/oauth_provider.py` — `build_token_refresh_context` already resolves mapping's `oauth_token_id` via `org_id`; verify and add runtime helper `get_token_for_org(db, integration_id, org_id) -> OAuthToken | None` with mapping-first-then-integration-fallback semantics
- `api/src/jobs/schedulers/oauth_token_refresh.py` — write status to `OAuthToken` not `OAuthProvider`; provider status only updated when the token belongs to the integration-level (fallback) connection

### Frontend — modified files
- `client/src/components/integrations/IntegrationMappingsTab.tsx` — remove `hasDataProvider` empty-state guard; add Connect button + status badge column; add `entity_id` text input column when no data provider
- `client/src/components/integrations/IntegrationMappingsTab.test.tsx` — new tests for the per-row Connect flow and no-data-provider rendering
- `client/src/components/integrations/IntegrationOverview.tsx` — relabel Connect button to "Default connection (used when an org isn't individually connected)"
- `client/src/services/integrations.ts` — add `authorizeMapping(integrationId, mappingId)`, `disconnectMapping(integrationId, mappingId)`, extend `IntegrationMapping` type with connection status fields
- `client/src/services/integrations.test.ts` — test new service methods

### Frontend — new files
- `client/e2e/per-mapping-oauth.spec.ts` — Playwright smoke test exercising "Connect" button → mocked callback → mapped status badge

---

## Phase 1: Schema + State Encoding

### Task 1: Migration — add per-token status and entity_id_source

**Files:**
- Create: `api/alembic/versions/<timestamp>_add_per_token_status_and_entity_id_source.py`
- Modify: `api/src/models/orm/oauth.py`

- [ ] **Step 1: Generate the migration skeleton**

Run: `cd api && alembic revision -m "add per-token status and entity_id_source"`

Note the generated revision filename. Open it.

- [ ] **Step 2: Fill in the migration body**

Replace `upgrade()` and `downgrade()` with:

```python
def upgrade() -> None:
    op.add_column(
        "oauth_tokens",
        sa.Column("status", sa.String(50), nullable=False, server_default="not_connected"),
    )
    op.add_column(
        "oauth_tokens",
        sa.Column("status_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "oauth_tokens",
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oauth_providers",
        sa.Column(
            "entity_id_source",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # Backfill existing tokens: completed if not expired, expired otherwise.
    op.execute("""
        UPDATE oauth_tokens
        SET status = CASE
            WHEN expires_at IS NULL OR expires_at > NOW() THEN 'completed'
            ELSE 'expired'
        END
        WHERE status = 'not_connected'
    """)


def downgrade() -> None:
    op.drop_column("oauth_providers", "entity_id_source")
    op.drop_column("oauth_tokens", "last_refresh_at")
    op.drop_column("oauth_tokens", "status_message")
    op.drop_column("oauth_tokens", "status")
```

Add at top of file:
```python
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 3: Update ORM models to match**

In `api/src/models/orm/oauth.py`, inside `class OAuthToken`, add after `scopes`:

```python
status: Mapped[str] = mapped_column(String(50), default="not_connected", server_default=text("'not_connected'"))
status_message: Mapped[str | None] = mapped_column(Text, default=None)
last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```

In `class OAuthProvider`, add after `token_url_defaults`:

```python
entity_id_source: Mapped[dict | None] = mapped_column(
    JSONB,
    default=None,
    comment="Where to extract entity_id from OAuth callback artifacts; shape: {type: 'url_param'|'id_token_claim'|'token_response_field', key: '...'}",
)
```

- [ ] **Step 4: Apply migration and verify**

Run: `docker compose restart bifrost-init && docker compose restart api`

Then: `docker compose exec postgres psql -U postgres -d bifrost -c "\d oauth_tokens" | grep -E "(status|last_refresh_at)"`

Expected: three new columns visible.

- [ ] **Step 5: Commit**

```bash
git add api/alembic/versions/ api/src/models/orm/oauth.py
git commit -m "feat(oauth): add per-token status fields and entity_id_source to provider"
```

---

### Task 2: Signed OAuth state token helpers

**Files:**
- Create: `api/src/services/oauth_state.py`
- Create: `api/tests/unit/test_oauth_state.py`

- [ ] **Step 1: Write the failing tests**

Create `api/tests/unit/test_oauth_state.py`:

```python
import time
import pytest
from src.services.oauth_state import encode_state, decode_state, OAuthStateError


def test_round_trip_no_mapping():
    token = encode_state({"provider_id": "abc"})
    payload = decode_state(token)
    assert payload["provider_id"] == "abc"
    assert payload.get("mapping_id") is None
    assert "nonce" in payload


def test_round_trip_with_mapping():
    token = encode_state({"provider_id": "abc", "mapping_id": "xyz"})
    payload = decode_state(token)
    assert payload["mapping_id"] == "xyz"


def test_tampered_state_rejected():
    token = encode_state({"provider_id": "abc"})
    # Flip one byte in the body (before the signature)
    body, sig = token.rsplit(".", 1)
    tampered = body[:-1] + ("0" if body[-1] != "0" else "1") + "." + sig
    with pytest.raises(OAuthStateError):
        decode_state(tampered)


def test_expired_state_rejected(monkeypatch):
    token = encode_state({"provider_id": "abc"}, ttl_seconds=1)
    monkeypatch.setattr("src.services.oauth_state.time.time", lambda: time.time() + 10)
    with pytest.raises(OAuthStateError):
        decode_state(token)


def test_decode_missing_signature_rejected():
    with pytest.raises(OAuthStateError):
        decode_state("notavalidtoken")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_oauth_state.py -v`

Expected: ImportError — `oauth_state` doesn't exist yet.

- [ ] **Step 3: Implement the helper**

Create `api/src/services/oauth_state.py`:

```python
"""Signed, timestamped state tokens for OAuth authorize/callback round-trip.

Carries optional `mapping_id` so the callback can attribute the resulting
token to a specific IntegrationMapping. HMAC-signed against
`OAUTH_STATE_SECRET` so the callback can trust the payload without
storing nonces server-side.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_DEFAULT_TTL = 600  # 10 minutes


class OAuthStateError(Exception):
    """Raised when state decoding fails (bad signature, expired, malformed)."""


def _secret() -> bytes:
    raw = os.environ.get("OAUTH_STATE_SECRET")
    if not raw:
        raise RuntimeError("OAUTH_STATE_SECRET env var must be set")
    return raw.encode()


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def encode_state(payload: dict, ttl_seconds: int = _DEFAULT_TTL) -> str:
    """Encode `payload` as a signed, timestamped state token.

    Adds `nonce` and `exp` automatically; do not pass them.
    """
    body = dict(payload)
    body["nonce"] = secrets.token_urlsafe(16)
    body["exp"] = int(time.time()) + ttl_seconds
    encoded_body = _b64url_encode(json.dumps(body, sort_keys=True).encode())
    sig = hmac.new(_secret(), encoded_body.encode(), hashlib.sha256).digest()
    return f"{encoded_body}.{_b64url_encode(sig)}"


def decode_state(token: str) -> dict:
    """Verify signature + expiry and return the decoded payload."""
    if "." not in token:
        raise OAuthStateError("malformed state token")
    encoded_body, encoded_sig = token.rsplit(".", 1)
    expected_sig = hmac.new(_secret(), encoded_body.encode(), hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(encoded_sig)
    except Exception as e:
        raise OAuthStateError("malformed signature") from e
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise OAuthStateError("invalid signature")
    try:
        payload = json.loads(_b64url_decode(encoded_body))
    except Exception as e:
        raise OAuthStateError("malformed payload") from e
    if payload.get("exp", 0) < int(time.time()):
        raise OAuthStateError("state token expired")
    return payload
```

- [ ] **Step 4: Set the secret in dev/test env**

In `docker-compose.dev.yml`, add to the `api` service `environment:` block:

```yaml
OAUTH_STATE_SECRET: dev-oauth-state-secret-do-not-use-in-prod
```

Do the same in `docker-compose.test.yml` (search for the api service environment block).

Restart so tests pick it up: `docker compose restart api`

- [ ] **Step 5: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_oauth_state.py -v`

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/oauth_state.py api/tests/unit/test_oauth_state.py docker-compose.dev.yml docker-compose.test.yml
git commit -m "feat(oauth): add signed state token helpers for callback round-trip"
```

---

### Task 3: entity_id extraction helper

**Files:**
- Create: `api/src/services/oauth_entity_id.py`
- Create: `api/tests/unit/test_oauth_entity_id.py`

- [ ] **Step 1: Write the failing tests**

Create `api/tests/unit/test_oauth_entity_id.py`:

```python
import base64
import json

from src.services.oauth_entity_id import extract_entity_id


def _make_id_token(claims: dict) -> str:
    """Build an unsigned JWT-like string with the given claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}."


def test_returns_none_when_no_config():
    assert extract_entity_id(None, callback_url_params={}, token_response={}) is None


def test_url_param_extraction():
    source = {"type": "url_param", "key": "realmId"}
    result = extract_entity_id(source, callback_url_params={"realmId": "12345"}, token_response={})
    assert result == "12345"


def test_url_param_missing_returns_none():
    source = {"type": "url_param", "key": "realmId"}
    result = extract_entity_id(source, callback_url_params={}, token_response={})
    assert result is None


def test_token_response_field_extraction():
    source = {"type": "token_response_field", "key": "stripe_user_id"}
    result = extract_entity_id(source, callback_url_params={}, token_response={"stripe_user_id": "acct_1"})
    assert result == "acct_1"


def test_token_response_dotted_path():
    source = {"type": "token_response_field", "key": "team.id"}
    result = extract_entity_id(
        source, callback_url_params={}, token_response={"team": {"id": "T123"}}
    )
    assert result == "T123"


def test_id_token_claim_extraction():
    source = {"type": "id_token_claim", "key": "tid"}
    id_token = _make_id_token({"tid": "tenant-uuid", "sub": "user"})
    result = extract_entity_id(
        source, callback_url_params={}, token_response={"id_token": id_token}
    )
    assert result == "tenant-uuid"


def test_id_token_claim_missing_id_token_returns_none():
    source = {"type": "id_token_claim", "key": "tid"}
    result = extract_entity_id(source, callback_url_params={}, token_response={})
    assert result is None


def test_unknown_type_returns_none():
    source = {"type": "future_source", "key": "x"}
    assert extract_entity_id(source, callback_url_params={}, token_response={}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_oauth_entity_id.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement extractor**

Create `api/src/services/oauth_entity_id.py`:

```python
"""Capture entity_id from OAuth callback artifacts based on provider config.

Driven by `OAuthProvider.entity_id_source`, a JSON dict of shape:
    {"type": "url_param" | "id_token_claim" | "token_response_field", "key": "..."}

The `key` may be a dotted path (e.g. `team.id`) for nested fields.
"""

import base64
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _lookup_dotted(d: dict[str, Any], key: str) -> Any:
    current: Any = d
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _decode_id_token_claims(id_token: str) -> dict[str, Any] | None:
    try:
        _, payload_b64, _ = id_token.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    except Exception as e:
        logger.warning(f"Failed to decode id_token claims: {e}")
        return None


def extract_entity_id(
    source: dict[str, Any] | None,
    callback_url_params: dict[str, str],
    token_response: dict[str, Any],
) -> str | None:
    """Return entity_id captured from the configured source, or None."""
    if not source:
        return None
    source_type = source.get("type")
    key = source.get("key")
    if not key:
        return None

    if source_type == "url_param":
        return callback_url_params.get(key)

    if source_type == "token_response_field":
        value = _lookup_dotted(token_response, key)
        return str(value) if value is not None else None

    if source_type == "id_token_claim":
        id_token = token_response.get("id_token")
        if not id_token:
            return None
        claims = _decode_id_token_claims(id_token)
        if not claims:
            return None
        value = _lookup_dotted(claims, key)
        return str(value) if value is not None else None

    logger.warning(f"Unknown entity_id_source type: {source_type}")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_oauth_entity_id.py -v`

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/oauth_entity_id.py api/tests/unit/test_oauth_entity_id.py
git commit -m "feat(oauth): add config-driven entity_id extractor"
```

---

## Phase 2: Per-Mapping Authorize + Callback

### Task 4: Per-mapping authorize endpoint

**Files:**
- Modify: `api/src/routers/integrations.py`
- Modify: `api/src/models/contracts/integrations.py`
- Modify: `api/src/routers/oauth_connections.py` (refactor `authorize_connection` to delegate state-building)

- [ ] **Step 1: Write the failing e2e test**

Create `api/tests/e2e/oauth/test_per_mapping_connect.py` (create directories as needed):

```python
"""E2E: per-mapping OAuth authorize endpoint returns a URL with our state token."""

import pytest
from urllib.parse import urlparse, parse_qs

pytestmark = pytest.mark.asyncio


async def test_authorize_for_mapping_returns_signed_state(
    async_client, seed_integration_with_oauth, seed_org, superuser_headers
):
    integration = seed_integration_with_oauth(
        authorization_url="https://login.example.com/authorize",
        oauth_flow_type="authorization_code",
    )
    org = seed_org()
    # Create a mapping (no token yet)
    mapping_resp = await async_client.post(
        f"/api/integrations/{integration.id}/mappings",
        json={"organization_id": str(org.id), "entity_id": "", "entity_name": ""},
        headers=superuser_headers,
    )
    assert mapping_resp.status_code == 201
    mapping_id = mapping_resp.json()["id"]

    # Request authorize URL for this mapping
    resp = await async_client.post(
        f"/api/integrations/{integration.id}/mappings/{mapping_id}/oauth/authorize",
        json={"redirect_uri": "http://localhost:3000/callback"},
        headers=superuser_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "authorization_url" in body

    parsed = urlparse(body["authorization_url"])
    qs = parse_qs(parsed.query)
    assert "state" in qs
    # The state must be our signed token (contains a "." separating body + sig)
    assert "." in qs["state"][0]

    # And it must round-trip back to our mapping
    from src.services.oauth_state import decode_state
    payload = decode_state(qs["state"][0])
    assert payload["mapping_id"] == mapping_id
    assert payload["provider_id"] == str(integration.oauth_provider.id)
```

You'll need fixtures `seed_integration_with_oauth`, `seed_org`, `superuser_headers`. Check `api/tests/conftest.py` and `api/tests/e2e/conftest.py` — use existing fixtures if present, add minimal ones if not.

- [ ] **Step 2: Run the test to verify it fails**

Run: `./test.sh tests/e2e/oauth/test_per_mapping_connect.py::test_authorize_for_mapping_returns_signed_state -v`

Expected: 404 — endpoint doesn't exist.

- [ ] **Step 3: Add the contract model**

In `api/src/models/contracts/integrations.py`, add near the other mapping models:

```python
class MappingAuthorizeRequest(BaseModel):
    """Request to begin OAuth authorize flow for a specific mapping."""
    redirect_uri: str = Field(..., description="Frontend callback URL")


class MappingAuthorizeResponse(BaseModel):
    """Response with the authorization URL to redirect the user to."""
    authorization_url: str
```

- [ ] **Step 4: Add the endpoint**

In `api/src/routers/integrations.py`, add a new endpoint near the existing mapping endpoints (after the mapping batch endpoint around line 1243):

```python
@router.post(
    "/{integration_id}/mappings/{mapping_id}/oauth/authorize",
    response_model=MappingAuthorizeResponse,
    summary="Begin OAuth authorize flow for a mapping",
    description="Returns the authorization URL with a signed state token carrying mapping_id (Platform admin only)",
)
async def authorize_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    request: MappingAuthorizeRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> MappingAuthorizeResponse:
    from urllib.parse import urlencode
    from src.services.oauth_state import encode_state
    from src.services.oauth_provider import get_url_resolution_defaults, resolve_url_template

    repo = IntegrationsRepository(ctx.db)
    integration = await repo.get_integration_by_id(integration_id)
    if not integration or not integration.oauth_provider:
        raise HTTPException(status_code=404, detail="Integration or its OAuth provider not found")
    provider = integration.oauth_provider
    if not provider.authorization_url:
        raise HTTPException(
            status_code=400,
            detail="This integration uses client_credentials and doesn't require user authorization",
        )

    mapping = await repo.get_mapping_by_id(integration_id, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    defaults = await get_url_resolution_defaults(ctx.db, provider)
    resolved_url = resolve_url_template(url=provider.authorization_url, defaults=defaults)
    state = encode_state({
        "provider_id": str(provider.id),
        "mapping_id": str(mapping_id),
    })
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "state": state,
        "scope": " ".join(provider.scopes) if provider.scopes else "",
        "redirect_uri": request.redirect_uri,
    }
    return MappingAuthorizeResponse(
        authorization_url=f"{resolved_url}?{urlencode(params)}",
    )
```

Add imports at top of file if missing:

```python
from src.models.contracts.integrations import (
    MappingAuthorizeRequest,
    MappingAuthorizeResponse,
)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `./test.sh tests/e2e/oauth/test_per_mapping_connect.py::test_authorize_for_mapping_returns_signed_state -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/integrations.py api/src/models/contracts/integrations.py api/tests/e2e/oauth/
git commit -m "feat(oauth): add per-mapping authorize endpoint with signed state"
```

---

### Task 5: Callback decodes state and links token to mapping

**Files:**
- Modify: `api/src/routers/oauth_connections.py` (oauth_callback handler around line 776)
- Create: `api/tests/unit/test_oauth_per_mapping_callback.py`

- [ ] **Step 1: Write the failing unit test**

Create `api/tests/unit/test_oauth_per_mapping_callback.py`:

```python
"""Unit-level test for the callback's mapping resolution + entity_id capture.

We test the helper that the handler delegates to so we can avoid spinning
the whole HTTP stack and mocking the external token endpoint.
"""

import pytest
from uuid import uuid4
from src.routers.oauth_connections import _apply_callback_to_mapping


@pytest.mark.asyncio
async def test_callback_links_token_to_mapping_and_captures_entity_id(
    db_session, seed_provider_with_entity_id_source, seed_org, seed_mapping
):
    provider = seed_provider_with_entity_id_source(
        entity_id_source={"type": "url_param", "key": "realmId"},
    )
    org = seed_org()
    mapping = seed_mapping(provider.integration_id, org.id, entity_id="")

    # Simulate having just stored a token via repo.store_token; pass its id in.
    from src.models.orm import OAuthToken
    token = OAuthToken(
        organization_id=org.id,
        provider_id=provider.id,
        encrypted_access_token=b"x",
        scopes=[],
    )
    db_session.add(token)
    await db_session.flush()

    await _apply_callback_to_mapping(
        db=db_session,
        mapping_id=mapping.id,
        token=token,
        provider=provider,
        callback_url_params={"realmId": "9999"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(mapping)
    assert mapping.oauth_token_id == token.id
    assert mapping.entity_id == "9999"


@pytest.mark.asyncio
async def test_callback_does_not_overwrite_existing_entity_id(
    db_session, seed_provider_with_entity_id_source, seed_org, seed_mapping
):
    provider = seed_provider_with_entity_id_source(
        entity_id_source={"type": "url_param", "key": "realmId"},
    )
    org = seed_org()
    mapping = seed_mapping(provider.integration_id, org.id, entity_id="manual-override")

    from src.models.orm import OAuthToken
    token = OAuthToken(
        organization_id=org.id,
        provider_id=provider.id,
        encrypted_access_token=b"x",
        scopes=[],
    )
    db_session.add(token)
    await db_session.flush()

    await _apply_callback_to_mapping(
        db=db_session,
        mapping_id=mapping.id,
        token=token,
        provider=provider,
        callback_url_params={"realmId": "9999"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(mapping)
    assert mapping.entity_id == "manual-override"  # not overwritten
```

If the necessary seed fixtures don't exist in `api/tests/unit/conftest.py`, add them — minimal factory functions that create rows in `db_session`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_oauth_per_mapping_callback.py -v`

Expected: ImportError — `_apply_callback_to_mapping` doesn't exist.

- [ ] **Step 3: Implement the helper and wire the callback**

In `api/src/routers/oauth_connections.py`, add this helper function (somewhere above the `oauth_callback` handler, around line 770):

```python
async def _apply_callback_to_mapping(
    db,
    mapping_id: UUID,
    token,  # OAuthToken row already persisted
    provider,  # OAuthProvider
    callback_url_params: dict[str, str],
    token_response: dict[str, Any],
) -> None:
    """Link the freshly-stored token to the mapping and capture entity_id.

    Idempotent for the `oauth_token_id` link. Does NOT overwrite a non-empty
    `mapping.entity_id` — manual overrides win over auto-capture.
    """
    from src.models.orm import IntegrationMapping
    from src.services.oauth_entity_id import extract_entity_id

    mapping = await db.get(IntegrationMapping, mapping_id)
    if not mapping:
        return  # silently skip — the connection still happened at the provider level

    mapping.oauth_token_id = token.id

    if not mapping.entity_id:
        captured = extract_entity_id(
            provider.entity_id_source,
            callback_url_params=callback_url_params,
            token_response=token_response,
        )
        if captured:
            mapping.entity_id = captured

    await db.flush()
```

Add `Any` to the imports at the top of the file if not present.

Now modify the existing `oauth_callback` handler. Find the request model `OAuthCallbackRequest` in `api/src/models/contracts/oauth.py` and add an optional field. Search: `grep -n "class OAuthCallbackRequest" api/src/models/contracts/oauth.py`. Add inside the class:

```python
callback_url_params: dict[str, str] | None = Field(
    default=None,
    description="Raw query params from the OAuth callback URL (used to capture entity_id)",
)
```

In `oauth_callback` (line 776 of `oauth_connections.py`), after `state` decoding (the request body already carries `state` — see `OAuthCallbackRequest` at line 61) and after the existing `repo.store_token` call (line 884), insert:

```python
    # If state carries a mapping_id, link the freshly-stored token to that mapping
    # and capture entity_id from the provider's configured source.
    mapping_id: UUID | None = None
    if request.state:
        try:
            from src.services.oauth_state import decode_state, OAuthStateError
            payload = decode_state(request.state)
            mid = payload.get("mapping_id")
            if mid:
                mapping_id = UUID(mid)
        except OAuthStateError as e:
            logger.warning(f"OAuth state decode failed (mapping link skipped): {e}")

    if mapping_id is not None:
        stored = await repo.get_token(connection_name, org_id)
        if stored:
            await _apply_callback_to_mapping(
                db=ctx.db,
                mapping_id=mapping_id,
                token=stored,
                provider=provider,
                callback_url_params=request.callback_url_params or {},
                token_response=result,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_oauth_per_mapping_callback.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/oauth_connections.py api/src/models/contracts/oauth.py api/tests/unit/test_oauth_per_mapping_callback.py
git commit -m "feat(oauth): callback links token to mapping and captures entity_id"
```

---

### Task 6: Per-mapping disconnect endpoint

**Files:**
- Modify: `api/src/routers/integrations.py`
- Modify: `api/tests/e2e/oauth/test_per_mapping_connect.py`

- [ ] **Step 1: Add the failing test**

Append to `api/tests/e2e/oauth/test_per_mapping_connect.py`:

```python
async def test_disconnect_mapping_clears_token_link_and_deletes_token(
    async_client, seed_integration_with_oauth, seed_org, seed_mapping_with_token, superuser_headers
):
    integration = seed_integration_with_oauth(oauth_flow_type="authorization_code")
    org = seed_org()
    mapping, token = seed_mapping_with_token(integration.id, org.id)
    assert mapping.oauth_token_id == token.id

    resp = await async_client.post(
        f"/api/integrations/{integration.id}/mappings/{mapping.id}/oauth/disconnect",
        headers=superuser_headers,
    )
    assert resp.status_code == 204

    # Mapping link cleared
    detail = await async_client.get(
        f"/api/integrations/{integration.id}/mappings/{mapping.id}",
        headers=superuser_headers,
    )
    assert detail.json()["oauth_token_id"] is None
```

Add `seed_mapping_with_token` fixture if needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/oauth/test_per_mapping_connect.py::test_disconnect_mapping_clears_token_link_and_deletes_token -v`

Expected: 404 / 405.

- [ ] **Step 3: Implement the endpoint**

In `api/src/routers/integrations.py`, after the `authorize_mapping` endpoint:

```python
@router.post(
    "/{integration_id}/mappings/{mapping_id}/oauth/disconnect",
    status_code=204,
    summary="Disconnect a mapping's per-row OAuth connection",
    description="Deletes the mapping's OAuth token and clears oauth_token_id. Fallback to integration-level token resumes (Platform admin only).",
)
async def disconnect_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> None:
    from src.models.orm import OAuthToken

    repo = IntegrationsRepository(ctx.db)
    mapping = await repo.get_mapping_by_id(integration_id, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    token_id = mapping.oauth_token_id
    mapping.oauth_token_id = None
    await ctx.db.flush()

    if token_id is not None:
        token = await ctx.db.get(OAuthToken, token_id)
        if token:
            await ctx.db.delete(token)
            await ctx.db.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/e2e/oauth/test_per_mapping_connect.py -v`

Expected: all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/integrations.py api/tests/e2e/oauth/test_per_mapping_connect.py
git commit -m "feat(oauth): add per-mapping disconnect endpoint"
```

---

## Phase 3: Runtime Token Resolution + Per-Token Status

### Task 7: Mapping-first runtime token lookup

**Files:**
- Modify: `api/src/services/oauth_provider.py`
- Create: `api/tests/unit/test_get_token_for_org.py`

- [ ] **Step 1: Write the failing tests**

Create `api/tests/unit/test_get_token_for_org.py`:

```python
"""Resolution priority for the workflow-runtime token lookup."""

import pytest
from uuid import uuid4
from src.services.oauth_provider import get_token_for_org

pytestmark = pytest.mark.asyncio


async def test_mapping_with_own_token_wins(
    db_session, seed_integration_with_oauth, seed_org, seed_mapping_with_token, seed_integration_level_token
):
    integration = seed_integration_with_oauth()
    org = seed_org()
    fallback = seed_integration_level_token(integration.oauth_provider.id)
    mapping, mapping_token = seed_mapping_with_token(integration.id, org.id)

    token = await get_token_for_org(db_session, integration.id, org.id)
    assert token is not None
    assert token.id == mapping_token.id
    assert token.id != fallback.id


async def test_falls_back_to_integration_token_when_mapping_unlinked(
    db_session, seed_integration_with_oauth, seed_org, seed_integration_level_token, seed_mapping
):
    integration = seed_integration_with_oauth()
    org = seed_org()
    fallback = seed_integration_level_token(integration.oauth_provider.id)
    seed_mapping(integration.id, org.id, oauth_token_id=None)

    token = await get_token_for_org(db_session, integration.id, org.id)
    assert token is not None
    assert token.id == fallback.id


async def test_falls_back_to_integration_token_when_no_mapping(
    db_session, seed_integration_with_oauth, seed_org, seed_integration_level_token
):
    integration = seed_integration_with_oauth()
    org = seed_org()
    fallback = seed_integration_level_token(integration.oauth_provider.id)

    token = await get_token_for_org(db_session, integration.id, org.id)
    assert token is not None
    assert token.id == fallback.id


async def test_returns_none_when_nothing_connected(
    db_session, seed_integration_with_oauth, seed_org
):
    integration = seed_integration_with_oauth()
    org = seed_org()

    token = await get_token_for_org(db_session, integration.id, org.id)
    assert token is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_get_token_for_org.py -v`

Expected: ImportError — `get_token_for_org` doesn't exist.

- [ ] **Step 3: Implement the helper**

In `api/src/services/oauth_provider.py`, add at the bottom of the file:

```python
async def get_token_for_org(
    db: "AsyncSession",
    integration_id: UUID,
    org_id: UUID,
) -> "OAuthToken | None":
    """Resolve the OAuth token to use for (integration, org) at workflow runtime.

    Priority:
    1. The integration mapping's `oauth_token_id` (per-row connect)
    2. The integration-level token (provider's most-recent token with
       organization_id IS NULL)
    3. None — caller must surface a clear error
    """
    from src.models.orm import Integration, IntegrationMapping, OAuthToken

    # 1. Mapping-scoped token
    mapping_result = await db.execute(
        select(IntegrationMapping).where(
            IntegrationMapping.integration_id == integration_id,
            IntegrationMapping.organization_id == org_id,
        )
    )
    mapping = mapping_result.scalar_one_or_none()
    if mapping and mapping.oauth_token_id:
        token = await db.get(OAuthToken, mapping.oauth_token_id)
        if token:
            return token

    # 2. Integration-level fallback
    integration_result = await db.execute(
        select(Integration).where(Integration.id == integration_id)
    )
    integration = integration_result.scalar_one_or_none()
    if not integration or not integration.oauth_provider_id:
        return None

    fallback_result = await db.execute(
        select(OAuthToken)
        .where(
            OAuthToken.provider_id == integration.oauth_provider_id,
            OAuthToken.organization_id.is_(None),
        )
        .order_by(OAuthToken.created_at.desc())
    )
    return fallback_result.scalar_one_or_none()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_get_token_for_org.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/oauth_provider.py api/tests/unit/test_get_token_for_org.py
git commit -m "feat(oauth): add mapping-first runtime token lookup"
```

---

### Task 8: Refresh scheduler writes per-token status

**Files:**
- Modify: `api/src/jobs/schedulers/oauth_token_refresh.py`
- Modify (or create): `api/tests/unit/test_oauth_token_refresh.py`

- [ ] **Step 1: Add or update the test**

Check if `api/tests/unit/test_oauth_token_refresh.py` exists. If yes, add tests below; if no, create with these tests:

```python
"""Refresh scheduler writes status to OAuthToken; OAuthProvider.status only
mirrors the integration-level token's outcome."""

import pytest
from datetime import datetime, timedelta, timezone

pytestmark = pytest.mark.asyncio


async def test_per_token_success_writes_token_status(
    db_session, monkeypatch, seed_provider, seed_org_scoped_token
):
    from src.jobs.schedulers import oauth_token_refresh as mod
    provider = seed_provider(oauth_flow_type="authorization_code")
    token = seed_org_scoped_token(
        provider.id, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
    )

    async def fake_http(td):
        return {
            "success": True,
            "token_id": td["token_id"],
            "provider_id": td["provider_id"],
            "encrypted_access_token": b"new-access",
            "encrypted_refresh_token": None,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "scopes": [],
        }
    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")

    await db_session.refresh(token)
    assert token.status == "completed"
    assert token.last_refresh_at is not None


async def test_per_token_failure_writes_token_status_message(
    db_session, monkeypatch, seed_provider, seed_org_scoped_token
):
    from src.jobs.schedulers import oauth_token_refresh as mod
    provider = seed_provider(oauth_flow_type="authorization_code")
    token = seed_org_scoped_token(provider.id, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    async def fake_http(td):
        return {
            "success": False,
            "token_id": td["token_id"],
            "provider_id": td["provider_id"],
            "error": "invalid_grant",
        }
    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")
    await db_session.refresh(token)
    assert token.status == "failed"
    assert token.status_message and "invalid_grant" in token.status_message


async def test_org_scoped_token_failure_does_not_touch_provider_status(
    db_session, monkeypatch, seed_provider, seed_org_scoped_token
):
    """One bad per-mapping token should not poison the integration-level provider status."""
    from src.jobs.schedulers import oauth_token_refresh as mod
    provider = seed_provider(oauth_flow_type="authorization_code")
    provider.status = "completed"
    provider.status_message = "good"
    await db_session.flush()

    seed_org_scoped_token(provider.id, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    async def fake_http(td):
        return {"success": False, "token_id": td["token_id"], "provider_id": td["provider_id"], "error": "x"}
    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")
    await db_session.refresh(provider)
    # Provider status untouched — only the org-scoped token failed
    assert provider.status == "completed"


async def test_integration_level_token_failure_updates_provider_status(
    db_session, monkeypatch, seed_provider, seed_integration_level_token
):
    """The integration-level token IS the provider's status surface."""
    from src.jobs.schedulers import oauth_token_refresh as mod
    provider = seed_provider(oauth_flow_type="authorization_code")
    seed_integration_level_token(provider.id, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    async def fake_http(td):
        return {"success": False, "token_id": td["token_id"], "provider_id": td["provider_id"], "error": "boom"}
    monkeypatch.setattr(mod, "refresh_oauth_token_http", fake_http)

    await mod.run_refresh_job(trigger_type="manual")
    await db_session.refresh(provider)
    assert provider.status == "failed"
    assert provider.status_message and "boom" in provider.status_message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_oauth_token_refresh.py -v`

Expected: assertion failures — current scheduler writes only to provider.

- [ ] **Step 3: Update the scheduler**

In `api/src/jobs/schedulers/oauth_token_refresh.py`, replace the Phase 3 block (lines 183-206) with:

```python
        # Phase 3: Persist refresh results (short-lived session)
        if refresh_outcomes:
            async with get_db_context() as db:
                for outcome in refresh_outcomes:
                    token = await db.get(OAuthToken, outcome["token_id"])
                    provider = await db.get(OAuthProvider, outcome["provider_id"])
                    if not token or not provider:
                        continue

                    # Per-token status always gets written
                    if outcome["success"]:
                        token.encrypted_access_token = outcome["encrypted_access_token"]
                        token.expires_at = outcome["expires_at"]
                        if outcome.get("encrypted_refresh_token"):
                            token.encrypted_refresh_token = outcome["encrypted_refresh_token"]
                        if outcome.get("scopes"):
                            token.scopes = outcome["scopes"]
                        token.status = "completed"
                        token.status_message = None
                        token.last_refresh_at = datetime.now(timezone.utc)
                    else:
                        token.status = "failed"
                        token.status_message = (outcome.get("error", "Refresh failed"))[:200]
                        token.last_refresh_at = datetime.now(timezone.utc)

                    # Provider status mirrors the integration-level (fallback) token only
                    if token.organization_id is None:
                        if outcome["success"]:
                            provider.status = "completed"
                            provider.status_message = None
                            provider.last_token_refresh = datetime.now(timezone.utc)
                        else:
                            provider.status = "failed"
                            provider.status_message = (outcome.get("error", "Refresh failed"))[:200]

                await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_oauth_token_refresh.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add api/src/jobs/schedulers/oauth_token_refresh.py api/tests/unit/test_oauth_token_refresh.py
git commit -m "feat(oauth): refresh scheduler writes per-token status; provider mirrors fallback only"
```

---

### Task 9: Mapping list response exposes per-token status

**Files:**
- Modify: `api/src/routers/integrations.py` (list_mappings around line 1023; get_mapping around line 1064; batch endpoint; integration detail around line 749)
- Modify: `api/src/models/contracts/integrations.py` (`IntegrationMappingResponse`)
- Modify: `api/tests/e2e/oauth/test_per_mapping_connect.py`

- [ ] **Step 1: Write the failing test**

Append to `api/tests/e2e/oauth/test_per_mapping_connect.py`:

```python
async def test_mapping_list_includes_connection_status(
    async_client, seed_integration_with_oauth, seed_org, seed_mapping_with_token, superuser_headers
):
    integration = seed_integration_with_oauth()
    org = seed_org()
    seed_mapping_with_token(integration.id, org.id, token_status="completed")

    resp = await async_client.get(
        f"/api/integrations/{integration.id}/mappings",
        headers=superuser_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["connection_status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/oauth/test_per_mapping_connect.py::test_mapping_list_includes_connection_status -v`

Expected: KeyError / missing field.

- [ ] **Step 3: Extend the response model**

In `api/src/models/contracts/integrations.py`, find `IntegrationMappingResponse` and add fields:

```python
connection_status: str | None = Field(
    default=None,
    description="Per-mapping OAuth token status (mirrors OAuthToken.status); None if no per-row token",
)
connection_message: str | None = Field(
    default=None,
    description="Last status message from the per-mapping token (e.g., refresh error)",
)
last_refresh_at: datetime | None = Field(
    default=None,
    description="When the per-mapping token was last refreshed",
)
```

- [ ] **Step 4: Populate the new fields in all mapping responses**

In `api/src/routers/integrations.py`, find each construction of `IntegrationMappingResponse` (search: `grep -n "IntegrationMappingResponse(" api/src/routers/integrations.py`). For each call site, add the three new fields. A helper is cleaner — add this near the top of the file:

```python
async def _mapping_to_response(
    db,
    m,  # IntegrationMapping
    config: dict | None = None,
) -> IntegrationMappingResponse:
    """Build IntegrationMappingResponse, hydrating per-token status."""
    from src.models.orm import OAuthToken
    status = None
    message = None
    last_refresh = None
    if m.oauth_token_id:
        token = await db.get(OAuthToken, m.oauth_token_id)
        if token:
            status = token.status
            message = token.status_message
            last_refresh = token.last_refresh_at
    return IntegrationMappingResponse(
        id=m.id,
        integration_id=m.integration_id,
        organization_id=m.organization_id,
        entity_id=m.entity_id,
        entity_name=m.entity_name,
        oauth_token_id=m.oauth_token_id,
        config=config,
        connection_status=status,
        connection_message=message,
        last_refresh_at=last_refresh,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )
```

Then update each `IntegrationMappingResponse(...)` call site to await `_mapping_to_response(ctx.db, m, config=...)`. Call sites identified earlier (line 749, 1010, 1042, 1083, 1127, 1170 — verify with grep). For the list endpoint (line 1041-1054), change to:

```python
    items = [await _mapping_to_response(ctx.db, m) for m in mappings]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./test.sh tests/e2e/oauth/test_per_mapping_connect.py -v`

Expected: all tests in file pass.

Also run the broader mapping suite to catch regressions:

Run: `./test.sh tests/e2e -k mapping -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/integrations.py api/src/models/contracts/integrations.py api/tests/e2e/oauth/test_per_mapping_connect.py
git commit -m "feat(integrations): expose per-mapping OAuth status in mapping responses"
```

---

## Phase 4: Frontend — Per-Row Connect

### Task 10: Regenerate types and extend service layer

**Files:**
- Run: `cd client && npm run generate:types`
- Modify: `client/src/services/integrations.ts`
- Create: `client/src/services/integrations.test.ts` (or extend existing)

- [ ] **Step 1: Regenerate API types**

Make sure dev stack is up:

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh
```

Then (URL may be a non-default port — check `./debug.sh status` if so):

```bash
cd client && npm run generate:types
```

Verify the new fields exist:

```bash
grep -A2 "connection_status" src/lib/v1.d.ts | head -10
```

Expected: type definitions for `connection_status`, `connection_message`, `last_refresh_at`.

- [ ] **Step 2: Write failing service tests**

Find `client/src/services/integrations.ts`. If a test file doesn't exist beside it, create `client/src/services/integrations.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { authorizeMapping, disconnectMapping } from "./integrations";

const mockPost = vi.fn();
const mockDelete = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiClient: {
    post: (...args: unknown[]) => mockPost(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

beforeEach(() => {
  mockPost.mockReset();
  mockDelete.mockReset();
});

describe("authorizeMapping", () => {
  it("POSTs to mapping authorize endpoint with redirect_uri", async () => {
    mockPost.mockResolvedValue({ authorization_url: "https://example.com/authz?state=abc" });
    const r = await authorizeMapping("integ-1", "map-1", "http://localhost:3000/cb");
    expect(mockPost).toHaveBeenCalledWith(
      "/api/integrations/integ-1/mappings/map-1/oauth/authorize",
      { redirect_uri: "http://localhost:3000/cb" },
    );
    expect(r.authorization_url).toContain("https://");
  });
});

describe("disconnectMapping", () => {
  it("POSTs to disconnect endpoint", async () => {
    mockPost.mockResolvedValue(undefined);
    await disconnectMapping("integ-1", "map-1");
    expect(mockPost).toHaveBeenCalledWith(
      "/api/integrations/integ-1/mappings/map-1/oauth/disconnect",
    );
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./test.sh client unit src/services/integrations.test.ts`

Expected: function-not-exported errors.

- [ ] **Step 4: Add the service functions**

In `client/src/services/integrations.ts`, add (matching the file's existing patterns):

```typescript
import type { components } from "@/lib/v1";

export type MappingAuthorizeResponse =
  components["schemas"]["MappingAuthorizeResponse"];

export async function authorizeMapping(
  integrationId: string,
  mappingId: string,
  redirectUri: string,
): Promise<MappingAuthorizeResponse> {
  return apiClient.post<MappingAuthorizeResponse>(
    `/api/integrations/${integrationId}/mappings/${mappingId}/oauth/authorize`,
    { redirect_uri: redirectUri },
  );
}

export async function disconnectMapping(
  integrationId: string,
  mappingId: string,
): Promise<void> {
  await apiClient.post<void>(
    `/api/integrations/${integrationId}/mappings/${mappingId}/oauth/disconnect`,
  );
}
```

If the file's existing functions use `$api.useMutation`-style hooks instead of raw `apiClient`, follow the existing pattern. Inspect first.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./test.sh client unit src/services/integrations.test.ts`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add client/src/services/integrations.ts client/src/services/integrations.test.ts client/src/lib/v1.d.ts
git commit -m "feat(client): add per-mapping OAuth service methods + regenerate types"
```

---

### Task 11: Per-row Connect + status column in mapping table

**Files:**
- Modify: `client/src/components/integrations/IntegrationMappingsTab.tsx`
- Modify: `client/src/components/integrations/IntegrationMappingsTab.test.tsx`

- [ ] **Step 1: Add the failing tests**

Open `client/src/components/integrations/IntegrationMappingsTab.test.tsx`. Add these tests:

```typescript
it("shows entity_id text input when hasDataProvider is false", () => {
  render(<IntegrationMappingsTab {...defaultProps} hasDataProvider={false} />);
  expect(screen.queryByText(/No Data Provider Configured/)).not.toBeInTheDocument();
  expect(screen.getByPlaceholderText(/entity id/i)).toBeInTheDocument();
});

it("renders Connect button when integration has OAuth and mapping has no token", () => {
  const props = {
    ...defaultProps,
    hasOAuth: true,
    orgsWithMappings: [{
      id: "org-1", name: "Org 1",
      mapping: { id: "m-1", oauth_token_id: null, connection_status: null } as IntegrationMapping,
      formData: { organization_id: "org-1", entity_id: "", entity_name: "", config: {} },
    }],
  };
  render(<IntegrationMappingsTab {...props} />);
  expect(screen.getByRole("button", { name: /connect/i })).toBeInTheDocument();
});

it("renders status badge from connection_status when mapping has a token", () => {
  const props = {
    ...defaultProps,
    hasOAuth: true,
    orgsWithMappings: [{
      id: "org-1", name: "Org 1",
      mapping: {
        id: "m-1",
        oauth_token_id: "tok-1",
        connection_status: "completed",
      } as IntegrationMapping,
      formData: { organization_id: "org-1", entity_id: "x", entity_name: "X", config: {} },
    }],
  };
  render(<IntegrationMappingsTab {...props} />);
  expect(screen.getByText(/connected/i)).toBeInTheDocument();
});

it("calls onConnectMapping when Connect button is clicked", async () => {
  const onConnectMapping = vi.fn();
  const props = {
    ...defaultProps,
    hasOAuth: true,
    onConnectMapping,
    orgsWithMappings: [{
      id: "org-1", name: "Org 1",
      mapping: { id: "m-1", oauth_token_id: null, connection_status: null } as IntegrationMapping,
      formData: { organization_id: "org-1", entity_id: "", entity_name: "", config: {} },
    }],
  };
  const { user } = renderWithUser(<IntegrationMappingsTab {...props} />);
  await user.click(screen.getByRole("button", { name: /connect/i }));
  expect(onConnectMapping).toHaveBeenCalledWith("m-1");
});
```

`defaultProps`, `renderWithUser`: use whatever's already in the file. If the file uses a different test scaffold, adapt.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh client unit src/components/integrations/IntegrationMappingsTab.test.tsx`

Expected: failures — no Connect button, no status badge from connection_status, empty-state still shown.

- [ ] **Step 3: Update the component**

Edit `client/src/components/integrations/IntegrationMappingsTab.tsx`:

(a) Update the props interface — add `hasOAuth: boolean` and `onConnectMapping: (mappingId: string) => void`:

```typescript
export interface IntegrationMappingsTabProps {
  // ... existing props ...
  hasOAuth: boolean;
  onConnectMapping: (mappingId: string) => void;
  onDisconnectMapping: (mappingId: string) => void;
}
```

(b) Remove the `hasDataProvider` empty-state guard (lines 142-161). Replace with a smaller inline notice above the table:

```tsx
{!hasDataProvider && (
  <p className="text-sm text-muted-foreground mb-4">
    No data provider configured — entity IDs must be entered manually.
  </p>
)}
```

(c) In the External Entity cell, when `!hasDataProvider`, replace `EntitySelector` with a plain `Input`:

```tsx
{hasDataProvider ? (
  /* existing EntitySelector / MatchSuggestionBadge logic */
) : (
  <Input
    value={org.formData.entity_id}
    onChange={(e) => onUpdateOrgMapping(org.id, e.target.value, e.target.value)}
    placeholder="Entity ID"
  />
)}
```

Import `Input` from `@/components/ui/input`.

(d) Add a "Connection" status column header between "Status" and "Actions":

```tsx
<DataTableHead className="w-32">Connection</DataTableHead>
```

(e) Add the cell. Status mapping: `completed` → green "Connected", `failed` → red "Failed", `expired` → yellow "Expired", `null` and OAuth available → render `<Button>Connect</Button>`, `null` and no OAuth → "—".

```tsx
<DataTableCell>
  {!hasOAuth ? (
    <span className="text-xs text-muted-foreground">—</span>
  ) : org.mapping?.connection_status === "completed" ? (
    <Badge className="bg-green-600">Connected</Badge>
  ) : org.mapping?.connection_status === "failed" ? (
    <Badge variant="destructive" title={org.mapping?.connection_message ?? ""}>
      Failed
    </Badge>
  ) : org.mapping?.connection_status === "expired" ? (
    <Badge className="bg-yellow-600">Expired</Badge>
  ) : org.mapping ? (
    <Button
      size="sm"
      variant="outline"
      onClick={() => onConnectMapping(org.mapping!.id)}
    >
      Connect
    </Button>
  ) : (
    <span className="text-xs text-muted-foreground">Save row first</span>
  )}
</DataTableCell>
```

(f) Add a "Disconnect" action button in the Actions cell, **separate from the existing Unlink button**. Two distinct affordances:

- **Unlink** (existing, `Unlink` icon): deletes the entire mapping row. Wires up to `onDeleteMapping` (already in props).
- **Disconnect** (new, use `Plug` or `PlugZap` from lucide-react): clears only the OAuth connection — deletes the `OAuthToken` and clears `mapping.oauth_token_id`, but leaves the mapping itself intact.

```tsx
{org.mapping?.oauth_token_id && (
  <Button
    size="sm"
    variant="ghost"
    onClick={() => onDisconnectMapping(org.mapping!.id)}
    title="Disconnect OAuth"
  >
    <PlugZap className="h-4 w-4" />
  </Button>
)}
```

Order in the Actions cell: Settings (gear), Disconnect (plug, when applicable), Unlink (existing, always). Add `PlugZap` to the lucide-react imports at the top of the file.

- [ ] **Step 4: Wire up the parent component**

Find the parent of `IntegrationMappingsTab` (search: `grep -rn "IntegrationMappingsTab" client/src --include="*.tsx" | grep -v test`). Likely `client/src/pages/integrations/[id].tsx` or similar.

Add handler:

```typescript
const handleConnectMapping = async (mappingId: string) => {
  const redirectUri = `${window.location.origin}/oauth/callback`;
  const { authorization_url } = await authorizeMapping(integrationId, mappingId, redirectUri);
  window.location.href = authorization_url;
};

const handleDisconnectMapping = async (mappingId: string) => {
  await disconnectMapping(integrationId, mappingId);
  queryClient.invalidateQueries({ queryKey: ["integration-mappings", integrationId] });
};
```

Pass `hasOAuth={Boolean(integration.oauth_provider_id)}`, `onConnectMapping={handleConnectMapping}`, `onDisconnectMapping={handleDisconnectMapping}` to `<IntegrationMappingsTab>`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./test.sh client unit src/components/integrations/IntegrationMappingsTab.test.tsx`

Expected: PASS.

- [ ] **Step 6: Verify in the browser**

`./debug.sh status` — note the URL. Open it, navigate to an integration with OAuth, open the Mappings tab. Verify:
- Mapping table renders even without a data provider configured (manual entity_id input shown)
- For OAuth-enabled integrations, each mapped row shows a Connection column
- Connect button visible on a saved-but-not-connected mapping

If anything is off, fix before committing.

- [ ] **Step 7: Commit**

```bash
git add client/src/components/integrations/IntegrationMappingsTab.tsx client/src/components/integrations/IntegrationMappingsTab.test.tsx client/src/pages/integrations/
git commit -m "feat(client): per-row OAuth Connect button + status column on mappings"
```

---

### Task 12: Relabel the integration-level Connect button

**Files:**
- Modify: `client/src/components/integrations/IntegrationOverview.tsx`
- Modify: `client/src/components/integrations/IntegrationOverview.test.tsx`

- [ ] **Step 1: Update the failing test**

In `IntegrationOverview.test.tsx`, find the assertion that looks for the "Connect" button text. Update it to expect the new label:

```typescript
expect(screen.getByRole("button", { name: /default connection/i })).toBeInTheDocument();
```

Or whichever button — adapt to existing test structure.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh client unit src/components/integrations/IntegrationOverview.test.tsx`

Expected: button text mismatch.

- [ ] **Step 3: Update the button**

In `IntegrationOverview.tsx`, find the integration-level Connect button (search for `onOAuthConnect` invocation). Change its label from "Connect" to "Connect default" and add helper text below it:

```tsx
<Button onClick={onOAuthConnect}>Connect default</Button>
<p className="text-xs text-muted-foreground mt-1">
  Used when an organization isn't individually connected via its mapping.
</p>
```

For Reconnect / Refresh variants, keep "Reconnect default" / "Refresh default token" with similar helper text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh client unit src/components/integrations/IntegrationOverview.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/integrations/IntegrationOverview.tsx client/src/components/integrations/IntegrationOverview.test.tsx
git commit -m "feat(client): clarify integration-level Connect as fallback default"
```

---

## Phase 5: End-to-End Validation

### Task 13: Playwright smoke test for per-mapping Connect

**Files:**
- Create: `client/e2e/per-mapping-oauth.spec.ts`

- [ ] **Step 1: Write the smoke test**

Create `client/e2e/per-mapping-oauth.spec.ts` (mirror the structure of an existing spec — check `client/e2e/auth.unauth.spec.ts` or any integrations spec):

```typescript
import { test, expect } from "@playwright/test";

test.describe("Per-mapping OAuth", () => {
  test("shows mapping table and manual entity_id input when no data provider", async ({ page }) => {
    // Assumes test seed creates an integration without a data provider but with OAuth provider.
    // Adapt the seed setup to match what the existing e2e tests do.
    await page.goto("/integrations");
    await page.getByRole("link", { name: /test integration without dp/i }).click();
    await page.getByRole("tab", { name: /mappings/i }).click();
    await expect(page.getByPlaceholder(/entity id/i)).toBeVisible();
    await expect(page.getByText(/no data provider configured/i)).toBeVisible();
  });

  test("Connect button on mapping row redirects to authorize URL", async ({ page }) => {
    await page.goto("/integrations");
    await page.getByRole("link", { name: /test integration with oauth/i }).click();
    await page.getByRole("tab", { name: /mappings/i }).click();

    // Intercept the authorize POST so we don't hit the real provider
    await page.route("**/oauth/authorize", route =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ authorization_url: "https://example.com/authz" }),
      })
    );

    const navPromise = page.waitForURL(/example\.com\/authz/);
    await page.getByRole("button", { name: /^connect$/i }).first().click();
    await navPromise;
  });
});
```

- [ ] **Step 2: Run the spec**

Run: `./test.sh client e2e e2e/per-mapping-oauth.spec.ts`

Expected: passes. If it fails because of missing test seed data, add a seed step to the existing e2e fixture setup or skip the test with a comment pointing at the missing fixture work.

- [ ] **Step 3: Commit**

```bash
git add client/e2e/per-mapping-oauth.spec.ts
git commit -m "test(client): playwright smoke for per-mapping OAuth Connect"
```

---

### Task 14: Full verification gate

- [ ] **Step 1: Backend type + lint**

```bash
cd api && pyright
cd api && ruff check .
```

Expected: 0 errors. Fix any introduced.

- [ ] **Step 2: Regenerate types (in case any contract drift)**

```bash
cd client && npm run generate:types
cd client && npm run tsc
cd client && npm run lint
```

Expected: clean.

- [ ] **Step 3: Full test suites**

```bash
./test.sh stack up
./test.sh all
./test.sh client unit
./test.sh client e2e
```

Expected: all green.

- [ ] **Step 4: Manual smoke in browser**

`./debug.sh status` → open URL. Walk through:
- Create an integration with an OAuth provider (auth-code flow) and no data provider.
- Open Mappings tab — confirm table renders with manual entity_id input.
- Save a mapping for an org. Confirm "Connect" button appears.
- (Sanity only — actual OAuth flow needs a real provider.) Confirm the button POSTs to the new authorize endpoint via network tab.
- Disconnect — confirm `oauth_token_id` clears on the row.

- [ ] **Step 5: Commit any final cleanups**

```bash
git add -A
git status  # verify nothing unexpected
git diff --cached --stat
git commit -m "chore: final cleanups from verification pass" || echo "Nothing to commit"
```

---

## Notes for the Implementing Engineer

- **Hot reload caveat for migrations:** the migration in Task 1 needs `docker compose restart bifrost-init && docker compose restart api` — code hot-reload alone doesn't run alembic. See `CLAUDE.md` "Database Migrations" section.
- **`./test.sh e2e <path>` runs the whole e2e suite, not a filter** — pass `-k <expr>` instead if you want a subset.
- **JUnit output** lives at `/tmp/bifrost-<project>/test-results.xml` for parsing.
- **State token rotation:** `OAUTH_STATE_SECRET` should be rotated at release time in prod. Out of scope for this plan, but flag in the release runbook.
- **`status` enum values for tokens** are not formally typed — kept as string for migration flexibility. Real values used: `not_connected`, `completed`, `failed`, `expired`. Add a Literal type later if it becomes load-bearing.
- **`OAuthToken.organization_id IS NULL`** is the marker for "integration-level fallback token". Verified in `get_token_for_org`. If a future change introduces org-scoped tokens that aren't tied to a mapping, this assumption breaks — revisit.
- **Existing OAuth flow for integration-level Connect is untouched.** Task 5's callback logic only fires when `state` decodes to a payload with `mapping_id`. Backward compatible.
