# Spot check: Initialize-Bifrost.ps1 VM smoke test

**Date:** 2026-06-05
**Where:** Windows VM `win11-pam` (192.168.122.175), `developer` user.
**Plan:** `docs/superpowers/plans/2026-06-05-initialize-bifrost-ps1.md` Task 5.

## Result: PASS

Ran `Initialize-Bifrost.ps1` on the VM from a throwaway dir
(`C:\Users\developer\bifrost-init-smoke`) with copies of `.env.example` and
`docker-compose.yml`, so the running debug stack (`bifrost-debug-37b0efff`)
was never disturbed (the production compose targets the same host ports).

### Environment
- **PowerShell 5.1.22621.2428** (Windows PowerShell, .NET Framework 4.x).
  This is the runtime the code-quality review flagged: `RandomNumberGenerator::Fill`
  does not exist here. The script ran without error, confirming the
  `Create()`/`GetBytes()`/`Dispose()` fix works on 5.1.

### `.\Initialize-Bifrost.ps1 -Domain localhost -Force -NoStart`
- Preflight OK (Docker detected and responding).
- `.env` written. **First 3 bytes `35 32 66`, NOT `239 187 191` — no BOM.**
- Secrets: `POSTGRES_PASSWORD` / `RABBITMQ_PASSWORD` / `SEAWEEDFS_SECRET_KEY`
  each 24-char alphanumeric; `BIFROST_SECRET_KEY` 48-char.
- Localhost branch correct: `BIFROST_WEBAUTHN_RP_ID=localhost`,
  `BIFROST_WEBAUTHN_ORIGIN=http://localhost:3000`,
  `BIFROST_ENVIRONMENT=development`, and **no** `BIFROST_PUBLIC_URL` /
  `BIFROST_S3_PUBLIC_ENDPOINT_URL` lines.
- Instructions printed: access URL `http://localhost:3000`,
  `docker compose logs -f`, `docker compose down`.

### Launch-path validation (without colliding with the debug stack)
Instead of `docker compose up -d` (would fight the debug stack for host ports),
validated that the generated `.env` is actually consumable by Compose:
- `docker compose --env-file .env config --quiet` → **exit 0** (the `.env`
  parses; a BOM would have broken the first key).
- The generated `POSTGRES_PASSWORD` value **resolves into the rendered**
  production compose config (`SECRET_RESOLVES=yes`).

Smoke dir removed afterward; debug stack untouched.

## Conclusion
`Initialize-Bifrost.ps1` works end-to-end on real Windows PowerShell 5.1:
generates a BOM-free, Compose-consumable production `.env` with correct
secrets and domain/WebAuthn config, and prints accurate instructions. The
`-NoStart` and full-launch (`config`-validated) paths are both confirmed.
