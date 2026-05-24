# Codex Gateway OAuth Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first stable per-user ChatGPT/Codex account connection path for Codex Gateway, with device-code as the preferred onboarding mode and Codex auth-cache import as the Tuesday pilot fallback.

**Architecture:** Keep upstream token material inside the existing `codex_gateway_upstream_accounts` vault table. Add small API contracts, repository lifecycle methods, a focused service for parsing/validating Codex auth cache payloads, and user-authenticated routes for status/import/disconnect. Device-code endpoints return a supported-mode contract now and can be wired to a live OpenAI/Codex transport once the exact public token endpoints are proven.

**Tech Stack:** FastAPI, Pydantic shared DTOs, SQLAlchemy async repositories, existing Bifrost Fernet encryption helpers, pytest unit/e2e patterns.

---

## Task 1: OAuth Status And Import Contracts

**Files:**
- Modify: `api/shared/models.py`
- Modify: `api/src/models/contracts/codex_gateway.py`
- Modify: `api/src/models/contracts/__init__.py`
- Test: `api/tests/unit/routers/test_codex_gateway.py`

- [ ] Write failing router tests for `GET /api/codex-gateway/oauth/status` and `POST /api/codex-gateway/oauth/import-auth-cache`.
- [ ] Run the router tests and confirm they fail because endpoints/contracts do not exist.
- [ ] Add shared DTOs for connection status, import request, import response, disconnect response, and connect options.
- [ ] Re-export DTOs from `src.models.contracts.codex_gateway` and contracts `__init__`.
- [ ] Run the router tests and DTO flags.

## Task 2: Repository Lifecycle Methods

**Files:**
- Modify: `api/src/repositories/codex_gateway.py`
- Test: `api/tests/unit/repositories/test_codex_gateway_repository.py`

- [ ] Write failing repository tests for upsert/revoke upstream account without exposing plaintext token fields.
- [ ] Run repository tests and confirm failures are missing methods.
- [ ] Add `upsert_upstream_account_for_user` and `revoke_upstream_account_for_user` using existing encryption helpers and metadata timestamps.
- [ ] Run repository tests.

## Task 3: Auth Cache Parser Service

**Files:**
- Create: `api/src/services/codex_gateway/oauth.py`
- Test: `api/tests/unit/services/test_codex_gateway_oauth.py`

- [ ] Write failing service tests for Codex auth-cache shapes containing token material and optional account metadata.
- [ ] Write failing service tests for malformed payloads and token redaction.
- [ ] Implement a minimal parser that accepts dict payloads, extracts access/refresh/id token fields from known top-level and nested shapes, derives best-effort subject/email/workspace metadata, and raises a safe validation error for unusable payloads.
- [ ] Run service tests.

## Task 4: Routes And Audit

**Files:**
- Modify: `api/src/routers/codex_gateway.py`
- Test: `api/tests/unit/routers/test_codex_gateway.py`

- [ ] Wire status/import/disconnect routes through the repository and parser.
- [ ] Ensure responses never include upstream token material.
- [ ] Emit audit events for import/connect and disconnect.
- [ ] Run router tests.

## Task 5: Verification

**Files:**
- Test-only unless failures require small implementation fixes.

- [ ] Run focused unit tests for Codex Gateway repository/router/service plus DTO flags.
- [ ] Run ruff and pyright on changed files.
- [ ] Sync to pve-t340 VM lane and run focused stack-backed tests if route behavior needs integration proof.
- [ ] Commit, push, open PR ready for review.
