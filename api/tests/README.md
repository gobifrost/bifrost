# Testing Guide for Bifrost

## Two-Tier Test Structure

```
tests/
├── unit/           # Fast tests, no Docker required
│   ├── engine/     # Execution engine unit tests
│   ├── services/   # Service layer unit tests
│   ├── handlers/   # HTTP handler unit tests
│   └── ...
├── e2e/            # Tests requiring Docker stack (DB, Redis, S3)
│   ├── engine/     # Engine tests that need process execution
│   ├── platform/   # Platform service tests with real DB
│   ├── mcp/        # MCP protocol tests
│   ├── api-integration/  # API endpoint tests with real HTTP
│   └── ...
└── fixtures/       # Shared test fixtures
```

## Quick Start

```bash
# ALWAYS use test.sh - it manages Docker dependencies
./test.sh stack up                        # Boot the stack (per-worktree, long-lived)
./test.sh                                 # Run unit tests (fast default)
./test.sh unit                            # Same as above
./test.sh e2e                             # Run e2e tests
./test.sh all                             # Unit + e2e (mirrors CI)
./test.sh tests/e2e/platform/test_sdk_from_workflow.py  # Pass through to pytest
./test.sh all --coverage                  # Run with coverage report
```

**Do NOT run pytest directly** - integration tests need Docker infrastructure.

## Test Tiers

### Unit Tests (`tests/unit/`)
- Test business logic in isolation
- Mock all external dependencies (DB, Redis, S3)
- No Docker required
- Marker: `@pytest.mark.unit`

### E2E Tests (`tests/e2e/`)
- Test with real database, message queue, and services
- Docker stack started by `test.sh`
- Includes platform service tests, API endpoint tests, engine execution tests
- Marker: `@pytest.mark.e2e`

## How test.sh Works

`test.sh` runs in two phases:

1. **Phase 1:** Unit tests (`tests/unit/`) - no Docker services needed beyond the test runner
2. **Phase 2:** E2E tests (`tests/e2e/`) - starts API + worker, runs with full infrastructure

## Key Fixtures

### Session-scoped (shared across all tests)
- `setup_test_environment` - Sets env vars, creates workspace dirs
- `async_engine` / `async_session_factory` - SQLAlchemy async engine

### Function-scoped (fresh per test)
- `db_session` - Database session with automatic rollback
- `mock_redis` - Mock for unit tests
- `cleanup_workspace_files` - Workspace file cleanup

### Auth fixtures (`tests/fixtures/auth.py`)
- `create_test_jwt()` - Generate test JWT tokens
- `auth_headers()` / `org_headers()` - Request headers with auth

## Best Practices

1. Use `./test.sh` to run tests, never raw pytest
2. Mark tests: `@pytest.mark.unit` or `@pytest.mark.e2e`
3. Unit tests mock everything; e2e tests use real infrastructure
4. Each test gets fresh state via `db_session` rollback
5. Create data via fixtures, not direct SQL
