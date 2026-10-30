# Authority epoch and token upgrades

Access tokens are moving to carry the caller's authorization facts (spec:
`docs/superpowers/specs/2026-10-27-access-token-claims-design.md`; plan:
`docs/plans/2026-10-30-token-pr1-implementation-plan.md`). A token minted from
those facts is only safe while the facts have not changed, so the database
keeps one **authority epoch**: a random 64-bit value in `authz_versions` that
changes whenever a committed transaction changes a fact a token carries. A
token records the epoch it was minted under and is refused once the value
differs. Code: `api/src/services/authorization/authz.py`; migration:
`api/alembic/versions/20261030_authz_versions.py`.

This runbook covers the two operator duties that come with it: keeping the
epoch honest during database maintenance, and upgrading through the token
releases one at a time.

## How the epoch changes

Postgres triggers change it; no application code writes it.

- Every fact table (people, organizations' provider flag, roles, role
  assignments, boundaries and permissions, workflow powers and ownership,
  workflow grants, Solution permission requests and status, Agent Solution
  binding, service definitions, the platform default workflow mode, and the
  creation-time columns of runs) has deferred constraint triggers
  (`authz_epoch_ins`, `authz_epoch_upd`, `authz_epoch_del`) and a statement
  trigger (`authz_epoch_truncate`). They call `authz_epoch_bump_once()`, which
  replaces the epoch at most once per transaction.
- Run status, service attempt state and leases are read live on each request,
  so ending a run never changes the epoch.
- Values are compared for equality only. A restored backup or failover that
  brings back an older value also brings back the facts it described.

The `authz_versions` row cannot be deleted or truncated (a trigger raises).

## Maintenance rule: bump in the same transaction

Anything that bypasses triggers can change a fact without changing the epoch.
That includes `SET session_replication_role = replica`,
`ALTER TABLE … DISABLE TRIGGER`, and data-only restores such as
`pg_restore --disable-triggers`. The epoch must change **in the same
transaction** as the facts. Bumping in a later transaction leaves a window,
between the two commits, in which tokens minted before the change still pass
validation; if the second step is interrupted, that window lasts until those
tokens expire.

Run trigger-disabling maintenance as one transaction that ends with the bump:

```sql
BEGIN;
SET LOCAL session_replication_role = replica;  -- or ALTER TABLE … DISABLE TRIGGER
-- … the maintenance changes …
SELECT authz_epoch_bump_once();
COMMIT;
```

`authz_epoch_bump_once()` is a plain function, so it works while triggers are
disabled. If the change commits, so does the new epoch; if it rolls back,
neither happens.

When a tool commits on its own and cannot add the bump to its transaction
(for example a data-only `pg_restore`), keep authentication unavailable across
both steps instead: stop the API and workers (Compose:
`docker compose stop api worker`; Kubernetes: scale the API and worker
Deployments to zero), run the tool, run `SELECT authz_epoch_bump_once();`, and
only then start them again. No token is validated while the facts and the
epoch disagree.

Every outstanding access token refreshes on its next request after the bump.
Migrations are held to the same rule by
`api/tests/unit/authorization/test_authz_completeness.py`: a migration that
disables triggers must call `authz_epoch_bump_once()` after its last
trigger-disabling statement, in its own transaction (no `autocommit_block`).

If the row is ever missing (for example after a partial, table-by-table
restore), every token that depends on it is refused and an error is logged.
Restore it with a fresh random value:

```sql
INSERT INTO authz_versions (subject_kind, subject_id, version)
VALUES ('global', '00000000-0000-0000-0000-000000000000',
        ('x' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 12)
             || substr(replace(gen_random_uuid()::text, '-', ''), 18, 4))::bit(64)::bigint);
```

## Watching the bump rate

One global epoch suits the measured rate (about 20 authority changes a day).
Each bump is one update of the `authz_versions` row, so the cumulative count is:

```sql
SELECT n_tup_upd FROM pg_stat_user_tables WHERE relname = 'authz_versions';
```

Sample it hourly. Move to per-subject versions (the table is already keyed by
subject) if bumps exceed **30 in an hour** in any hour on three days of a week,
or if `refresh_required` responses exceed **5 %** of authenticated runtime
requests over a day.

## Upgrading through the token releases

The token change ships as consecutive releases. Each later release's migration
refuses to run until the release before it has fully rolled out, and the
database records that an API of each release started (`authz_protocol_state`).
Upgrade **one token release at a time**, confirm the rollout finished, and only
then take the next one. Never roll back below the first token release (the one
that added `authz_versions`): run refresh requests would fail closed.

### Docker Compose

`docker-compose.yml` names the Bifrost images as
`ghcr.io/gobifrost/bifrost-api:${BIFROST_VERSION:-latest}` (and the client
likewise). Run these steps from the directory that holds `docker-compose.yml`
and `.env`.

1. **Pin the release.** Set the release tag in `.env`:

   ```bash
   BIFROST_VERSION=v1.2.3
   ```

   To pin by digest instead, write full image references, from the release
   notes, in a `docker-compose.override.yml` next to `docker-compose.yml`:

   ```yaml
   services:
     init:
       image: ghcr.io/gobifrost/bifrost-api@sha256:<api digest>
     api:
       image: ghcr.io/gobifrost/bifrost-api@sha256:<api digest>
     worker:
       image: ghcr.io/gobifrost/bifrost-api@sha256:<api digest>
     client:
       image: ghcr.io/gobifrost/bifrost-client@sha256:<client digest>
   ```

   Check what Compose resolved (this reads `.env` and the override, so it does
   not depend on your shell's environment):

   ```bash
   docker compose config --images
   ```

2. **Pull the application images only.** PostgreSQL, PgBouncer, Redis and
   SeaweedFS are not pulled or changed:

   ```bash
   docker compose pull init api worker client
   ```

3. **Check the dependencies are already healthy.**

   ```bash
   docker compose ps postgres pgbouncer redis seaweedfs \
     --format '{{.Service}}: {{.State}} {{.Health}}'
   ```

   PostgreSQL, PgBouncer and Redis must show `running healthy`. SeaweedFS has
   no health check, so it shows only `running`; confirm its S3 port accepts
   connections from the running API container:

   ```bash
   docker compose exec api python -c \
     "import socket; socket.create_connection(('seaweedfs', 8333), 5); print('seaweedfs ok')"
   ```

   If any check fails, stop and fix that service first.

4. **Run the migrations alone.**

   ```bash
   docker compose run --rm --no-deps init
   ```

   `--no-deps` keeps Compose from recreating any dependency. If a migration
   refuses (for example "Upgrade to the … release and let it finish rolling
   out first"), stop here: the application containers still run the previous
   release, and only the failed migration's own transaction was attempted and
   rolled back.

   The authority-version migration waits at most 5 seconds for each of its
   table locks. Behind a long-running transaction it fails with
   `canceling statement due to lock timeout` (or `statement timeout`) rather
   than stalling traffic; the database stays at the previous revision. Run the
   same command again; if it keeps timing out, find the long transaction with
   `SELECT pid, now() - xact_start, query FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY 2 DESC;`.

5. **Replace the application containers.**

   ```bash
   docker compose up -d --no-deps api worker client
   ```

6. **Verify the running images.** A tag in `docker compose ps` is not enough:
   compare the image each container runs with the image Compose resolved, then
   compare that image's digest with the release notes.

   ```bash
   for service in api worker client; do
     want=$(docker compose config --format json | jq -r --arg s "$service" '.services[$s].image')
     want_id=$(docker image inspect --format '{{.Id}}' "$want")
     for container in $(docker compose ps -q "$service"); do
       running=$(docker inspect --format '{{.Image}}' "$container")
       [ "$running" = "$want_id" ] && echo "$service ok" || echo "$service MISMATCH: $container"
     done
     docker image inspect --format '{{join .RepoDigests "\n"}}' "$want"
   done
   ```

   Every container must print `ok`, the printed `RepoDigests` must include the
   digests published in the release notes for the API and client images, and
   `docker compose ps api worker client` must show them healthy. That is the
   confirmation that the rollout finished.

7. Take the next token release only in a later upgrade.

### Kubernetes (Flux)

The init Job (or initContainer) runs the migrations before the new pods start;
a refused migration blocks the rollout and the existing pods keep serving. A
lock timeout behaves the same way and is retried with the Job.
Confirm the rollout finished with `kubectl rollout status deploy/<api>` and the
worker controller showing only Jobs of the new version before taking the next
token release.

## Expired-token challenge

An expired access token that a refresh can fix (a person's token, or a run
token minted with authorization claims) is refused with:

```text
WWW-Authenticate: Bearer error="invalid_token", error_description="refresh_required"
```

The authentication dependency raises it before any route handler or later
dependency runs, so a client may refresh and replay the request. Other 401s
keep `WWW-Authenticate: Bearer`.
