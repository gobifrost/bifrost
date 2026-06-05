# Initialize-Bifrost.ps1 — Windows deploy entry point

**Date:** 2026-06-05
**Status:** Approved design, ready for implementation plan

## Purpose

A single PowerShell entry point for an **operator deploying Bifrost on Windows**.
It is the Windows counterpart to `setup.sh`, but finishes the job: generate a
production `.env` (secrets + domain/WebAuthn config), bring the stack up with
`docker compose up -d`, and print access instructions.

Mental model agreed with the user:

- **Development** happens on Linux/macOS, where the existing Bash scripts
  (`setup.sh`, `debug.sh`, `test.sh`) are fine.
- **Windows is a deployment target.** The one script a Windows operator runs by
  hand is setup. So we port `setup.sh` only — not `debug.sh` (no dev loop on
  Windows) and not `test.sh` (CI/dev tooling).

The script must not depend on `openssl`, `sed`, Git Bash, or WSL — a fresh
Windows deploy box may have none of them. Everything is PowerShell-native.

## Naming & placement

- **File:** `Initialize-Bifrost.ps1` in the **repo root**, next to `setup.sh`.
- **Function:** `Initialize-Bifrost`. Approved PowerShell verb `Initialize-`
  ("prepare a resource for use"); bringing the stack up is part of
  initialization, the way `Initialize-Disk` leaves you with a usable disk.
- The script **defines** the function and **invokes** it at the bottom, so it
  works as `.\Initialize-Bifrost.ps1` and is also dot-sourceable for testing.

## Behavior (the C option: faithful port + deploy-box guards)

The `.env` contract, prompts, secret lengths, and derived values are a 1:1
match with `setup.sh`. Added on top are three deploy-box guards (Docker
running, `.env.example` present, overwrite confirmation) and the launch +
instructions step.

### Flow

1. **Preflight.**
   - `docker` must be on PATH and the daemon must respond (`docker info`).
     On failure: clear remediation ("Start Docker Desktop and retry") and a
     non-zero exit. This is the #1 deploy-box failure mode.
   - `.env.example` must exist in the working directory (error if not).
2. **Overwrite guard.** If `.env` already exists, prompt to overwrite
   (default **No**), matching `setup.sh`. `-Force` skips the prompt.
3. **Domain prompt.** Read the domain (default `localhost`). Light validation:
   non-empty, no whitespace, no scheme prefix (`http://`). `-Domain` supplies
   it non-interactively.
   - Derive `ORIGIN` / `ENVIRONMENT` exactly as `setup.sh`:
     - `localhost` or `127.0.0.1` → `http://<domain>:3000`, `development`
     - otherwise → `https://<domain>`, `production`
4. **Generate secrets.** `System.Security.Cryptography.RandomNumberGenerator`
   → base64 → strip to `[A-Za-z0-9]`, same lengths as the bash script:
   - `POSTGRES_PASSWORD` (24), `RABBITMQ_PASSWORD` (24),
     `SEAWEEDFS_SECRET_KEY` (24), `BIFROST_SECRET_KEY` (48).
5. **Write `.env`.** Copy `.env.example`, then rewrite these keys via
   line-by-line regex replace (no `sed`):
   - Always: `POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD`, `SEAWEEDFS_SECRET_KEY`,
     `BIFROST_SECRET_KEY`, `BIFROST_WEBAUTHN_RP_ID` (= domain),
     `BIFROST_WEBAUTHN_ORIGIN` (= origin), `BIFROST_ENVIRONMENT`.
   - Non-localhost only: set `BIFROST_PUBLIC_URL=https://<domain>` and
     uncomment/set `BIFROST_S3_PUBLIC_ENDPOINT_URL=/s3`.
   - **Write UTF-8 without BOM.** A BOM breaks Docker Compose `env_file`
     parsing (the first key gets a `﻿` prefix). This is the single most
     important encoding detail in the script.
6. **Launch.** `docker compose up -d`. On non-zero exit, surface stderr and
   stop with a non-zero code. `-NoStart` skips this step (generate `.env`
   only).
7. **Print instructions.** Mirror `setup.sh`'s summary (what was generated /
   configured) and then next steps:
   - Access URL: `http://localhost:3000` (localhost) or `https://<domain>`.
   - View logs: `docker compose logs -f`.
   - Stop: `docker compose down`.

### Parameters

All optional; a bare invocation is fully interactive like `setup.sh`.

- `-Domain <string>` — supply the domain non-interactively.
- `-Force` — skip the `.env` overwrite prompt.
- `-NoStart` — generate `.env` but do not run `docker compose up -d`.

## `.env` key contract

Confirmed present in `.env.example` (line numbers as of this spec):
`POSTGRES_PASSWORD` (13), `RABBITMQ_PASSWORD` (14), `BIFROST_ENVIRONMENT` (19),
`BIFROST_SECRET_KEY` (35), `BIFROST_WEBAUTHN_RP_ID` (77),
`BIFROST_WEBAUTHN_ORIGIN` (80), `SEAWEEDFS_SECRET_KEY` (113),
`BIFROST_S3_PUBLIC_ENDPOINT_URL` (121, commented), `BIFROST_PUBLIC_URL` (130).

## Out of scope / parallel changes

- **No `debug.sh` port** — development stays on Linux/macOS.
- **No `test.sh` port** — CI/dev tooling, used by `.github/workflows/ci.yml`.
- **Delete `build.sh`** — the team no longer does manual releases (CI builds
  images via `docker/build-push` in `ci.yml`). Verified during planning:
  `build.sh` has **zero references** outside one historical `docs/` plan, and
  the `bifrost:release` skill is **already** fully CI-based (it never mentions
  `build.sh`). So the cleanup is just the file deletion — there is nothing to
  strip from the release skill. The historical `docs/` reference stays as a
  completed-plan record. This is a parallel change on the same branch,
  independent of the `.ps1` work.

## Verification

**VM smoke test only** (per user). On the `win11-pam` VM:

1. Run `.\Initialize-Bifrost.ps1` (interactive, domain `localhost`).
2. Confirm `.env` is generated with the right keys, real secrets, no BOM.
3. Confirm `docker compose up -d` brings the stack up and it serves
   (health endpoint returns 200).
4. Confirm the printed instructions are accurate.

No Pester tests for this iteration. (If the pure-logic functions — secret
generation, key rewriting, origin derivation — are factored cleanly, Pester
coverage can be added later without rework, but it is not required now.)

## Risks / notes

- **BOM** is the classic PowerShell-writes-a-file footgun for `.env`; the spec
  calls it out explicitly. Use `[System.IO.File]::WriteAllText` with a
  `UTF8Encoding($false)` (no BOM), not `Out-File`/`Set-Content` defaults.
- **CRLF in `.env`** is harmless for Docker Compose `env_file` (it trims), but
  we'll write LF for cleanliness and consistency with `setup.sh` output.
- The script assumes it runs from the **repo root** (where `.env.example` and
  the compose file live), same assumption as `setup.sh`.
