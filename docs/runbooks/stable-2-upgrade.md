# Upgrading From 1.4.1 to 2.0

An existing 1.4.1 installation must start the authority-protocol bridge before
upgrading to 2.0. A direct upgrade is deliberately rejected by
`20261031_authz_claims_gate`. A fresh installation can start at 2.0.

This upgrade also changes human authentication. Reserve a maintenance window:
old and new APIs must not serve together. Every person signs in again afterward,
and existing V2 Apps need their SDK updated before reopening. Passwords, MFA
enrollments, passkeys, and stored integration credentials are retained.

## Before Maintenance

- Back up PostgreSQL and object storage together, record the currently running
  images, and verify the backups can be restored. Preserve the platform secret;
  changing it also affects encrypted integration credentials.
- Inventory existing V2 Apps. Confirm retained source or original deployment
  source is available for their SDK rebuilds.
- Inventory RabbitMQ queues, including historical and poison queues. Keep the
  broker, its data, and `BIFROST_RABBITMQ_URL` available through recovery. Do not
  delete queues or replay poison messages as part of this upgrade.
- Obtain an API-matched CLI. The 1.4.1 CLI cannot parse newer execution
  responses. See [Human Authentication Cutover](human-auth-cutover.md) and
  [Application SDK Updates](application-sdk-update.md).

## Required Bridge

The bridge is the published PR1a API build:

```text
ghcr.io/gobifrost/bifrost-api:1.4.2-dev.1793357148@sha256:9ca80613869946a0fcad769cdfe355ccaa6968a624df9f38b232c1702a9bda9a
```

Its source is `cc1cd64fb80aac69885df0db00ba12f61522dacd`. Pin this full
reference for init and API services during the bridge phase. Do not substitute
the moving `dev` or `latest` tag. Keep the 1.4.1 client unavailable during this
phase; a bridge client is not needed for the maintenance steps.

1. Block ordinary API and MCP ingress, drain requests and WebSockets, and stop
   old APIs, workers, and schedulers. Preserve the broker and dependencies.
2. Run the bridge init against the existing database, then start its API.
   Starting the real API is essential: migrations alone do not record the
   protocol rollout. Confirm the following returns one row:

   ```sql
   SELECT protocol, first_started_at
   FROM authz_protocol_state WHERE protocol = 1;
   ```

3. The bridge already includes broker recovery. Allow its canonical drain to
   finish; it requires a 120-second quiet interval after the queues become
   empty. Confirm the durable record exists:

   ```sql
   SELECT value_json FROM system_configs
   WHERE category = 'runtime' AND key = 'legacy_broker_drained'
     AND organization_id IS NULL;
   ```

   Account for every queue and unacknowledged message before retiring the
   broker. Recovery supports workflow executions, agent runs, summaries, and
   summary backfills. Poison messages stay put. A queued run whose old Redis
   context expired fails visibly; a drain record does not prove every old run
   succeeded. Investigate failures before proceeding.
4. Stop the bridge API. Never create a protocol row manually or bypass the
   migration guard.

## Activate 2.0

Follow the complete [Human Authentication Cutover](human-auth-cutover.md)
procedure while ingress remains blocked:

1. Pin the selected 2.0 API and client artifacts by their published immutable
   digests. Use the same API artifact for init, API, worker, and scheduler.
2. Apply the forward migrations and start the new serving fleet. Verify every
   API replica and exposed MCP mount runs the selected build. Keep older human
   issuers and validators inaccessible.
3. Update each existing V2 App with `bifrost apps sdk update APP`. Verify its
   PlatformJob completed and the active SDK provenance records contract 3.
   Use controlled operator access and the selected build worker; do not
   reconcile unrelated Solution entities.
4. Start the selected workers and scheduler. Check recovered executions and
   their recorded-user lineage. Remove the legacy broker configuration only
   after recovery and queue accounting are complete.
5. Reopen ingress and verify login/MFA, refresh rotation, revoke-all, MCP
   reconnect, WebSocket reconnect, and an ordinary workflow. People must sign
   in again in browsers, CLI, and MCP clients. Used or revoked refresh
   credentials must fail.

After human-auth activation, rolling back below the cutover is unsupported:
old validators could accept revoked legacy credentials. Use a forward repair
build. A lower-version disaster recovery needs a separately reviewed revocation
plan. See [Authority Epoch](authz-epoch.md) for trigger-disabling maintenance and
backup-restore rules.

## Disposable Image Rehearsal

The release coordinator runs the following against an explicitly selected API
digest before publishing. It uses an isolated Compose project, synthetic data,
and no host ports. Evidence must be outside the checkout:

```bash
python3 scripts/release/stable_2_upgrade_rehearsal.py \
  --run-id stable2-qualification \
  --target-api-image ghcr.io/gobifrost/bifrost-api@sha256:TARGET_DIGEST \
  --target-source FULL_SOURCE_COMMIT \
  --evidence-dir /absolute/existing/evidence-directory
```

The rehearsal verifies source witnesses, queues work through the real 1.4.1
HTTP API, proves direct-upgrade refusal on a cloned database, starts the real
bridge, and checks durable recovery, lineage, terminal success, and queue
accounting on the target. The receipt distinguishes a qualified source build
from the final versioned release artifact. It does not replace the production
maintenance boundary or App SDK verification.
