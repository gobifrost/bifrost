# Bifrost Integrations Platform

MSP automation platform built with FastAPI and React.

## Worktree rule (CRITICAL)

All changes — features, fixes, refactors, doc edits — must be made in a git worktree, never directly on `main` (or any shared branch) in the primary checkout. If a task is about to modify files and the current working directory is the primary `bifrost` checkout, stop and create or enter a worktree first. Use `EnterWorktree` (or `git worktree add .claude/worktrees/<name>`). The only edits permitted on `main` are ones the user explicitly requests be made there.

## Org scoping (CRITICAL)

If you are touching code that reads or writes anything with an `organization_id` column, **read `api/src/repositories/README.md` before you write code.** That file is the single source of truth.

The short version:

- Every execution-resolution entity (Config, Table, OAuth tokens, etc.) goes through `OrgScopedRepository`. There is exactly one cascade primitive and it lives in the base class. Do not write inline `WHERE organization_id == x OR organization_id IS NULL` queries in routers — the lint test catches this.
- The scope resolver is `api/shared/scope_resolver.py::resolve_effective_scope`. Four rules: UNSET → caller's default org; explicit `None` → bypass-only (global); caller's own org → always allowed; any other UUID → bypass-only. UNSET and explicit `None` are NOT the same. **"Bypass" means `is_platform_admin OR is_provider_org`** — two independent flags, either of which grants cross-org/global scope (a platform admin in any org, OR a non-admin member of the provider org). Don't collapse it to "platform admin only." See `api/src/repositories/README.md` ("Why two independent bypass flags?") for the full table.
- Two repository methods: `get(name=...)` returns one row with cascade-and-override; `list()` returns the cascade union (and applies role filter when the repo was constructed with a regular user). User-ness lives on the repository instance, not the method.
- Identity entities (Organization, User, UserRole, OAuthAccount, AuditLog) do NOT go through this pattern. They belong to an org but are never resolved by name with cascade.
- MCP authenticates as the user directly and does not follow the engine-sentinel pattern.

When in doubt, read the README. When you find yourself reinventing the cascade or the resolver, stop and use the canonical version.

## Spinning up the dev environment and connecting

Use this whenever you need a running Bifrost instance to exercise — clicking around, screenshots, browser testing, or driving the API directly via the CLI. Always do this from the worktree, not the primary checkout.

1. **Boot the stack** from the worktree root:
   ```bash
   ./debug.sh up
   ./debug.sh status
   ```
   Capture the URL from `./debug.sh status`. Under netbird mode (default when `NETBIRD_SETUP_KEY` is set), the host-reachable URL is the full `http://bifrost-debug-<name>-<n>-<n>.netbird.cloud` form — the short `http://bifrost-debug-<name>` form is only resolvable from inside the mesh. Under port mode, the URL is `http://localhost:<port>`.

2. **Connect via the CLI** from an isolated scratch directory outside the repo (not the worktree, not `~`). This keeps `.env`/credentials out of the source tree and lets you install the API-matched CLI without disturbing the user's global `bifrost` install:
   ```bash
   mkdir -p /tmp/bifrost-cli-<name>
   cd /tmp/bifrost-cli-<name>
   python3 -m venv .venv
   .venv/bin/pip install --quiet --upgrade pip
   .venv/bin/pip install --quiet "<API_URL>/api/cli/download"
   ```
   The download endpoint serves the build matching the running API; installing this version avoids the version-mismatch warning that otherwise short-circuits subcommand output.

3. **Log in** inside the scratch directory using password-grant. This writes `.env` (with `BIFROST_API_URL`, `BIFROST_ACCESS_TOKEN`, `BIFROST_REFRESH_TOKEN`) so subsequent commands in this directory pick the tokens up automatically:
   ```bash
   ./.venv/bin/bifrost login --url <API_URL> --email dev@gobifrost.com --password password
   ```
   Default credentials are `dev@gobifrost.com` / `password` (MFA off) — password-grant works only because the dev stack has MFA disabled.

4. **Drive the API** with `./.venv/bin/bifrost <entity> <command> ...`. Use `--help` on any subcommand. Browser testing goes against the URL from step 1.

Tips:
- If a CLI command appears to succeed silently, run the matching `list` to verify — version-mismatch warnings can otherwise mask failures (do not pipe to `/dev/null` blindly).
- The netbird sidecar can transiently drop the peer; if connections start failing, re-check `./debug.sh status` and re-fetch the URL.
- For seeding entities to exercise a UI change (e.g. enough apps/agents to make a page overflow), use `bifrost apps create`, `bifrost agents create`, etc., from this same scratch venv.

## Technologies

-   **Backend**: Python 3.11 (FastAPI), SQLAlchemy, Pydantic, PostgreSQL, RabbitMQ, Redis
-   **Frontend**: TypeScript 4.9+, React, Vite
-   **Storage**: PostgreSQL (data), Redis (cache/sessions), RabbitMQ (message queue)
-   **Infrastructure**: Docker, Docker Compose, GitHub Actions for CI/CD

## Development Environment (CRITICAL - READ FIRST)

### Everything Runs in Docker

**Development happens inside Docker containers, not on the host machine.**

Start the development stack (per-worktree isolated):
```bash
./debug.sh             # Boot stack, print URL + login
./debug.sh status      # Show URL, login, mode (port or netbird)
./debug.sh down        # Tear down + remove volumes for THIS worktree
./debug.sh logs api    # Follow logs for one service
```

`./debug.sh` derives its Compose project name from the worktree path, so multiple worktrees can run debug stacks in parallel. URL and login are printed at the end of `up` (default: `dev@gobifrost.com` / `password`, MFA off).

The default mode allocates a free local port for the client (deterministic per worktree, in 30000-39999). If `NETBIRD_SETUP_KEY` is set in `~/.config/bifrost/debug.env`, the stack boots with a Netbird sidecar instead and is reachable at `http://<bifrost-debug-WORKTREE>` over the Netbird mesh — no host ports.

Stack contains: API (port 8000 internal), Client (port 80 internal), Scheduler, Worker, Postgres, RabbitMQ, Redis, SeaweedFS. All Bifrost services build from `api/Dockerfile.dev` / `client/Dockerfile.dev` (source build, not public images).

### Hot Reload is Automatic

All services have hot reload - **DO NOT restart containers for code changes**:
- **API**: Uvicorn watches `/app/src` and `/app/shared` - restarts automatically
- **Client**: Vite HMR - updates instantly in browser
- **Scheduler/Worker**: watchmedo monitors Python files - restarts automatically

### Vite Proxies All API Requests

The client at `localhost:3000` proxies `/api/*` to the API container. This means:
- Access the app at `http://localhost:3000` (not 8000)
- Type generation works from `client/` directory while stack is running
- No CORS issues - everything goes through Vite

### Container Management Rules

| Scenario | Correct Action | Wrong Action |
|----------|---------------|--------------|
| Code changes | Do nothing (hot reload) | Restart containers |
| New Python dependency | `docker compose restart api` | Rebuild everything |
| Database migration | Restart `bifrost-init` (runs alembic), then restart `api` | Restart entire stack |
| Schema changes | Restart api, then `npm run generate:types` | Manual type updates |
| Something broken | Check logs first: `docker compose logs api` | Nuke and restart |

### DO NOT

- ❌ Run `docker compose up` if containers are already running
- ❌ Run pytest directly on host - use `./test.sh`
- ❌ Start a separate uvicorn/vite process outside Docker
- ❌ Restart the entire stack for single-service changes
- ❌ Manually write TypeScript types for API responses

## File Operation Model

| Content type | Write path | Read path | Cache strategy |
|-------------|-----------|----------|----------------|
| All files | S3 `_repo/` via `RepoStorage` | S3 `_repo/` via `RepoStorage` | S3 is source of truth |
| Text files | + `file_index` DB | `file_index` (search only) | Search index, never read content |
| Python modules/workflows | + Redis via `set_module()` | Redis via `get_module()` → S3 fallback | Write-through (warm on write) |
| Compiled app files | S3 `_apps/{id}/preview/` | Redis render cache → S3 fallback | Invalidate on write, lazy rebuild |

**`get_module()` must NOT be used for non-Python files.** App source reads (TSX, YAML, etc.) go to S3 directly via `RepoStorage.read()`.

### Form & agent inline manifest content — portability design

Form and agent **content lives inline under each UUID** in `.bifrost/forms.yaml` and `.bifrost/agents.yaml`. There are no per-UUID `.form.yaml` / `.agent.yaml` files anymore — the manifest is the source of truth for both identity and content.

The same portability rule still applies: the inline content intentionally **excludes environment-specific fields** (`access_level`, `organization_id`, `roles`, `created_by`, timestamps). Those live on the DB record and on the manifest entry alongside (but distinct from) the portable content. The portable content (fields, prompts, tool bindings, etc.) can be shared with the community or imported across environments without carrying org/role/access assumptions. **Do not add environment-specific fields to the inline manifest serialization** — they belong on the DB entity record / the manifest entry's env-specific section, not the portable content.

## Manifest Serialization & Git Sync (Integration Data)

The git sync system uses a **manifest** (`.bifrost/*.yaml`) to round-trip platform entities between the database and git. The `_resolve_*` methods in `github_sync.py` handle importing manifest data into the DB.

### Key files

| File | Purpose |
|------|---------|
| `api/src/services/manifest.py` | Pydantic models for manifest data (`ManifestIntegration`, `ManifestIntegrationMapping`, `ManifestConfig`, etc.) |
| `api/src/services/manifest_generator.py` | DB → manifest serialization (generates `.bifrost/*.yaml` from DB state) |
| `api/src/services/github_sync.py` | Manifest → DB import (`_resolve_integration`, `_resolve_config`, etc.) + stale-entity cleanup |

### Integration sync: what gets serialized

| Entity | Manifest model | DB model | Natural key for upsert |
|--------|---------------|----------|----------------------|
| Integration | `ManifestIntegration` | `Integration` | `name` or `id` |
| Config schema | `ManifestConfigSchemaItem` (list in integration) | `IntegrationConfigSchema` | `(integration_id, key)` |
| Mappings | `ManifestIntegrationMapping` (list in integration) | `IntegrationMapping` | `(integration_id, organization_id)` |
| Config values | `ManifestConfig` (separate `configs` dict) | `Config` | `id` (UUID) |

### App sync

`ManifestApp` in `.bifrost/apps.yaml` carries all app metadata (name, description, dependencies, access_level, roles). The `path` field points to the app source directory (e.g. `apps/my-app`), which contains only TSX/TS/CSS source code — no metadata files. App npm dependencies are stored in the `Application.dependencies` JSON column in the DB.

### Critical: non-destructive upsert pattern

`_resolve_integration` syncs config schema and mappings using **upsert-by-natural-key** (not delete-all + re-insert):

-   **Why**: `IntegrationConfigSchema` rows are referenced by `Config` rows via FK (`config_schema_id`). Deleting schema rows cascades to Config values set by users in the UI.
-   **Why**: `IntegrationMapping` rows carry `oauth_token_id` set by users via OAuth flow. Deleting and re-creating mappings loses this.
-   **Pattern**: Query existing rows → update matching → insert new → delete removed.
-   **Stale-entity cleanup**: Config rows with a `config_schema_id` (user-set integration values) are excluded from the "delete configs not in manifest" sweep, since their lifecycle is managed by IntegrationConfigSchema cascade.

### When adding new fields to manifest models

1. Add the field to the Pydantic model in `manifest.py` (e.g., `ManifestIntegrationMapping`)
2. Add serialization in `manifest_generator.py` (DB → manifest)
3. Add deserialization in `github_sync.py` `_resolve_*` method (manifest → DB)
4. **For upserted entities**: ensure the new field is included in BOTH the update-existing AND insert-new code paths
5. Write a round-trip unit test in `tests/unit/test_manifest.py` and an E2E test in `tests/e2e/platform/test_git_sync_local.py`

## Keeping CLI, MCP, and manifest in sync

Entity mutations have three parallel surfaces: **CLI** (`bifrost <entity> ...`), **MCP** (`api/src/services/mcp_server/tools/`), and **manifest export** (`.bifrost/*.yaml` via `bifrost sync` / `bifrost export`). All three read from the same `XxxCreate` / `XxxUpdate` Pydantic DTOs via `api/bifrost/dto_flags.py`, so drift is caught by tests rather than review discipline.

**When a DTO changes:**

1. Run the DTO-parity test: `./test.sh tests/unit/test_dto_flags.py`. If it fails, either add the new field to the appropriate CLI command / MCP tool, or add it to `DTO_EXCLUDES` in `api/bifrost/dto_flags.py` with a one-line comment explaining why (UI-managed, out-of-scope, etc.).
2. If the field should round-trip in portable exports, update `api/bifrost/manifest.py` (`ManifestXxx` pydantic models) and the scrub rules in `api/bifrost/portable.py`.
3. If the field changes a command or tool that Claude should know about, update `docs/llm.txt`.
4. **Run the contract-version tripwire: `./test.sh tests/unit/test_contract_version.py`.** It fingerprints every CLI/SDK-consumed DTO (command DTOs + all `src.models.contracts.cli` SDK DTOs, pulled in programmatically). If one changed, the test fails and forces a decision: if the change is **breaking** (field removed/renamed/retyped, a response shape the CLI parses), bump `CONTRACT_VERSION` in **both** `api/shared/contract_version.py` and `api/bifrost/contract_version.py`, then refresh `EXPECTED_CONTRACT_FINGERPRINT`. If it's **cosmetic/additive**, just refresh the fingerprint. This keeps a missed bump from shipping a CLI that silently breaks against the server — the CLI's runtime gate hard-blocks on a contract mismatch. (Pure route renames aren't gated — they 404 loudly rather than corrupt; the DTO layer catches the silent breakages.)

**When renaming or reassigning an entity (workflow, table, config):** grep the codebase before committing. Workflows are referenced by `path::func` in forms; tables are referenced by name in workflow SDK calls (`sdk.tables.get("...")`); configs are referenced by key. `bifrost tables update --name` warns on renames but does not block — the author is responsible for a full-workspace search (`rg -n '\b<old-name>\b' apps/ workflows/`) before pushing.

**`.bifrost/` is export-only.** Watch only syncs code (`apps/`, `workflows/*.py`); it does NOT push `.bifrost/` content. Entity mutations go through the CLI (`bifrost orgs create`, `bifrost roles update`, etc.) or MCP. To share an env's state across environments, use `bifrost export --portable <dir>` (scrubs env-specific fields) and `bifrost import <dir> --org <uuid> --role-mode name` (rewrites org/role refs against the target env).

**MCP vs REST routers (existing drift):** the MCP tools for `agents`, `forms`, `tables`, `apps`, `events` re-implement router logic and have diverged (different permission models, missing side effects, divergent validation). See `docs/plans/2026-04-18-mcp-router-reconciliation.md` for the catalog and reconciliation sequence. **New MCP tools must be thin HTTP wrappers that call the REST endpoints** (see `api/src/services/mcp_server/tools/roles.py` / `configs.py` / `_http_bridge.py` for the pattern) — no direct ORM access, no repository imports. A unit test (`api/tests/unit/test_mcp_thin_wrapper.py`) enforces this.

## Project Structure

```
api/
├── src/              # FastAPI application
│   ├── handlers/     # HTTP endpoint handlers (thin layer)
│   ├── models/       # SQLAlchemy models
│   ├── jobs/         # Background job workers
│   └── main.py       # FastAPI app entry point
├── shared/           # Business logic, utilities
│   ├── models.py     # Pydantic models (source of truth)
│   └── ...
├── alembic/          # Database migrations
└── tests/            # Unit and E2E tests

client/
├── src/
│   ├── services/     # API client wrappers
│   └── lib/
│       └── v1.d.ts   # Auto-generated TypeScript types
└── ...
```

## Project-Specific Rules

### No dead code, no unrequested fallbacks

Never leave dead code, commented-out code, unused helpers, or "just in case" fallback branches behind. Never add a fallback, compatibility shim, or alternate code path that the user did not explicitly ask for — these get forgotten and become latent bugs (see the `multiprocessing.spawn` fallback in `process_pool.py` that silently leaked ~800 MB per pod in production). If you think a fallback might be warranted, **ask first**. When removing a code path, also remove everything that was only reachable from it in the same change.

### Agent summary cost lives in `total_cost_7d`

Summarizer-generated `AIUsage` rows roll up into `AgentStats.total_cost_7d` (the Spend (7d) card). A backfill of N runs will move the card by roughly `N × (avg summarizer cost per run)`. The backfill endpoint (`POST /api/agent-runs/backfill-summaries`) shows a cost estimate up front — runbook at `docs/runbooks/agent-summary-backfill.md`.

### Backend (Python/FastAPI)

-   **Models**: All Pydantic models MUST be defined in `api/shared/models.py`
-   **Routing**: Create one handler file per base route (e.g., `/discovery` → `discovery_handlers.py`)
    -   Sub-routes and related functions live in the same file
-   **Request/Response**: Always use Pydantic Request and Response models
-   **Business Logic**: MUST live in `api/shared/`, NOT in `api/src/handlers/`
    -   Handlers are thin HTTP handlers only
    -   Complex logic, algorithms, business rules go in shared modules
    -   Example: User provisioning logic lives in `shared/user_provisioning.py`

### Frontend (TypeScript/React)

-   **Type Generation**: Run `npm run generate:types` in `client/` after API changes
    -   Must run while API is running
    -   Types are auto-generated from OpenAPI spec based on `models.py`
    -   Never manually write TypeScript types for API endpoints
-   **API Services**: Create service files in `client/src/services/` for new endpoints

Example service pattern:
```typescript
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type DataProvider = components["schemas"]["DataProviderMetadata"];
export type DataProviderResponse = components["schemas"]["DataProviderResponse"];

export async function getDataProviders() {
  return apiClient.get<DataProviderResponse>("/api/data-providers");
}
```

### Testing & Quality

-   **Tests**: All work requires tests. Backend logic → unit tests in `api/tests/unit/`. Endpoint/workflow/integration changes → e2e tests in `api/tests/e2e/`. React components → sibling `*.test.tsx` (vitest). User-facing features → happy-path spec in `client/e2e/` (Playwright).
    -   **Functional frontend modules require vitest coverage.** New or modified `.ts` files under `client/src/lib/**` and `client/src/services/**` that export functions (auth helpers, storage adapters, API wrappers, formatters, etc.) need a sibling `*.test.ts` covering the public API. Pure type/constant re-export files and files that only import and re-configure third-party SDKs are exempt. If the module has a cross-tab, cross-window, or storage-boundary concern (like `auth-token.ts`), the test MUST exercise that boundary — a regression that only reproduces with two tabs open is one a future refactor will silently re-introduce otherwise.
    -   **IMPORTANT**: Always use `./test.sh` — it manages the Dockerized test stack (PostgreSQL, Redis, RabbitMQ, SeaweedFS, API, worker). Running pytest directly on the host will FAIL for anything touching DB/queue/cache.
    -   **Stack lifecycle is separate from test execution.** Boot once per worktree, run tests many times. See the Commands section below.
    -   **Test results**: `./test.sh` writes JUnit XML to `/tmp/bifrost/test-results.xml` — parse this for pass/fail details instead of grepping stdout.
    -   **Logs**: Container logs are exported to `/tmp/bifrost-<project>/*.log` after test runs (per-worktree, so parallel worktrees don't clobber each other).
-   **Type Checking**: Must pass `pyright` (API) and `npm run tsc` (client)
-   **Linting**: Must pass `ruff check` (API) and `npm run lint` (client)

### Commands

```bash
# Dev stack (per-worktree)
./debug.sh                                         # Boot dev stack (hot reload)
./debug.sh status                                  # URL + login
./debug.sh down                                    # Tear down + wipe volumes
./debug.sh logs api                                # Follow one service's logs

# Test stack lifecycle (per worktree, long-lived)
./test.sh stack up                                 # Boot the test stack for this worktree
./test.sh stack down                               # Tear it down + remove volumes
./test.sh stack reset                              # Fast state reset (<2s) — DB clone + redis flush + object storage wipe
./test.sh stack status                             # Is the stack up? What project name?

# Backend tests (stack must be up; state auto-reset before each run)
./test.sh                                          # Unit tests only (fast default)
./test.sh unit                                     # Same
./test.sh e2e                                      # Backend e2e
./test.sh all                                      # Unit + e2e (mirrors CI)
./test.sh tests/unit/test_foo.py::test_bar -v      # Passthrough to pytest

# Client tests
./test.sh client unit                              # Vitest on host (no stack needed)
./test.sh client e2e                               # Playwright in containers
./test.sh client e2e --screenshots                 # Capture screenshots for every test (UX review)
./test.sh client e2e e2e/auth.unauth.spec.ts       # Passthrough to Playwright

# CI (one-shot: boot → all tests → tear down)
./test.sh ci

# Type Generation (requires dev stack running via ./debug.sh)
cd client && npm run generate:types       # Regenerate TypeScript types from API

# Quality Checks
cd api && pyright                         # Type check Python
cd api && ruff check .                    # Lint Python
cd client && npm run tsc                  # Type check TypeScript
cd client && npm run lint                 # Lint TypeScript
```

**Parallel worktrees:** Each git worktree gets its own isolated test stack (Compose project name is derived from the worktree path). Run `./test.sh stack up` in multiple worktrees simultaneously without conflict.

### Common Workflows

**After adding/modifying Pydantic models:**
1. Make changes to `api/shared/models.py`
2. Hot reload updates API automatically
3. Run `cd client && npm run generate:types`
4. TypeScript types updated in `client/src/lib/v1.d.ts`

**After creating a new migration:**
1. Create migration: `cd api && alembic revision -m "description"`
2. Edit the migration file
3. Restart `bifrost-init` to run alembic: `docker compose restart bifrost-init`
4. Restart API: `docker compose restart api`

**After adding a Python dependency:**
1. Add to `pyproject.toml` (root)
2. Regenerate the lock: `docker run --rm -v "$PWD":/repo -w /repo python:3.14-slim sh -c "pip install --quiet --require-hashes -r requirements-piptools.lock && pip-compile --generate-hashes --output-file=requirements.lock pyproject.toml"`
3. Rebuild and restart: `docker compose -f docker-compose.dev.yml up --build api`

**Merging main into a long-lived branch:** Two specific gotchas:

1. **`client/src/lib/v1.d.ts` always conflicts.** It's a generated file. Resolve by taking main's version, then regenerating against your running API:
   ```bash
   git checkout --theirs client/src/lib/v1.d.ts
   cd client && OPENAPI_URL=<your-dev-url>/openapi.json npm run generate:types
   cd .. && git add client/src/lib/v1.d.ts && git commit --no-edit
   ```
   Get the URL from `./debug.sh status`. The regen must happen *after* the merge is in place, because it reflects the merged code's schema.

2. **Newly-added Python deps on main break the dev container silently.** If main introduced a new `import` (e.g. `defusedxml`) and your container was built before that dep landed in `requirements.lock`, the API will start failing with `ModuleNotFoundError` after the next restart, and `/openapi.json` will 502. Symptom: regen suddenly can't fetch the schema. Fix:
   ```bash
   docker logs bifrost-debug-<project>-api-1 --tail 20   # confirm ModuleNotFoundError
   docker exec bifrost-debug-<project>-api-1 pip install <missing-pkg>
   docker restart bifrost-debug-<project>-api-1
   ```
   This is a per-container quick fix. The lasting fix is `./debug.sh down` and a fresh `./debug.sh` (which rebuilds against the new lock), but the in-container `pip install` unblocks you in 10 seconds.

## Pre-Completion Verification (REQUIRED)

Before marking any significant work complete, run this verification sequence:

```bash
# 1. Ensure debug stack is running for THIS worktree
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up

# 2. Backend checks (from api/ directory)
cd api
pyright                    # Type checking - must pass with 0 errors
ruff check .               # Linting - must pass

# 3. Regenerate frontend types (from client/ directory)
cd ../client
npm run generate:types     # Requires debug stack up. If client is bound to a non-default port,
                           # set OPENAPI_URL=http://localhost:<port>/openapi.json (see ./debug.sh status).

# 4. Frontend checks
npm run tsc                # Type checking - must pass
npm run lint               # Linting - must pass

# 5. Run tests
cd ..
./test.sh stack up         # boot if not already up (per-worktree)
./test.sh all              # backend unit + e2e
./test.sh client unit      # vitest component tests
./test.sh client e2e       # Playwright E2E (skip if no UI changes)
```

**This is mandatory for any changes that touch:**
- Backend API endpoints or models
- Frontend components or hooks
- Database schema or migrations
