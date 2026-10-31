# Bifrost Usage Reporting

This Cloudflare Worker receives one small, self-reported daily usage snapshot from a Bifrost installation. It exposes only two routes:

- `GET /health` returns `200 ok` and does not access D1.
- `POST /usage` validates a report and returns `204` only after its D1 write succeeds.

There is no reporting read route or administrative endpoint. The receiver does not authenticate a report, and its validation does not prove that a report came from a genuine installation. Open-source clients must not contain an authentication secret for this endpoint.

## Report contract

`POST /usage` requires `Content-Type: application/json` (an optional UTF-8 charset is accepted), no query parameters, and an uncompressed request body of at most 4096 bytes. The exact JSON object is:

```json
{
  "schema_version": 1,
  "installation_id": "a0b1c2d3-e4f5-4a67-8b9c-0d1e2f3a4b5c",
  "version": "1.2.3",
  "period": "2026-10-30",
  "workflow_runs": 12,
  "agent_runs": 3,
  "environment": "production"
}
```

All fields are required and no additional fields are accepted. `installation_id` must be a canonical UUIDv4; uppercase input is normalized to lowercase. `version` must be a bounded SemVer 2 version, including valid prerelease and build metadata. `period` must be a completed UTC day from the last 30 days. Counts are nonnegative safe integers no greater than `1,000,000,000`, and the receiver accepts only `environment: "production"`.

The D1 table stores `installation_id`, `period`, server-generated `received_at`, schema version, Bifrost version, and the two counts. `(installation_id, period)` is the primary key. Repeated reports replace the prior snapshot instead of incrementing it, so retries do not inflate counts.

Counts are daily snapshots grouped by UTC completion day. `workflow_runs` counts completed workflow executions, and `agent_runs` counts completed agent runs; both include terminal success, failure, and cancellation outcomes.

Malformed reports receive generic `400`, oversized streamed bodies receive `413`, unsupported media types or encodings receive `415`, and unavailable D1 receives `503`. Request bodies are parsed only for validation and are never stored as raw input. The Worker does not inspect or store addresses, request headers other than content controls, Cloudflare request metadata, user identifiers, organization identifiers, or IP data, and it produces no application logs. These application-level limits describe this Worker and D1 schema only; Cloudflare may still process traffic and security data under its own service terms, which this Worker cannot control.

## Local verification

Run this package through the repository’s Docker-backed reporting test entry point:

```bash
./test.sh reporting
```

It performs a clean dependency install, TypeScript check, Worker build, and Node’s built-in test runner. The tests boot Miniflare, apply `migrations/0001_usage_reports.sql` to an actual local D1 database, then exercise the Worker against it.

The runner uses Node 24 and mounts the Linux host's CA certificate bundle read-only, which workerd needs when initializing its network service. An existing Docker image with Node 24 or later can be selected explicitly with `BIFROST_REPORTING_TEST_IMAGE`; there is no automatic image substitution.

## Deploy

The production D1 ID is recorded in `wrangler.toml`. Authenticate Wrangler, apply any pending migrations, and deploy:

```bash
npm ci
npx wrangler d1 migrations apply bifrost-usage-reporting --remote
npx wrangler deploy
```

For a separate installation of this service, create a new D1 database with `npx wrangler d1 create <name>` and update the account, database, and custom-domain bindings before deploying. Never point a test deployment at the production database.

`wrangler.toml` pins the Bifrost account, `reports.gobifrost.com` custom domain, production-only deployment, and disabled Worker observability/logging settings. The account must have permission to create the D1 database and configure the custom domain.

After deployment, an operator with D1 access can inspect daily aggregate snapshots directly:

```bash
npx wrangler d1 execute bifrost-usage-reporting --remote --command \
  "SELECT period, COUNT(*) AS installations, SUM(workflow_runs) AS workflow_runs, SUM(agent_runs) AS agent_runs FROM usage_reports GROUP BY period ORDER BY period DESC"
```

Count active reporting installations over the last 30 completed UTC days:

```bash
npx wrangler d1 execute bifrost-usage-reporting --remote --command \
  "SELECT COUNT(DISTINCT installation_id) AS installations_30d FROM usage_reports WHERE period >= date('now', '-30 days') AND period < date('now')"
```

These are self-reported installations and execution totals, not verified customers or distinct people. Zero-run reports count as installations that are running but idle. The Bifrost sender is a separate change: it must persist one random installation ID per database, exclude development/testing/debug environments, and honor `BIFROST_USAGE_REPORTING_ENABLED=false` before making any reporting request.
