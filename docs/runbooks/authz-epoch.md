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

## Updating the Platform Image

Stable 1.4.1 installations upgrade directly to 2.0. There is no intermediate
API image or staged token-release prerequisite. Forward migrations create the
authority epoch, credential metadata, and durable human sessions in the normal
init step. People sign in again after the update; passwords, MFA enrollments,
passkeys, and encrypted integration credentials remain stored.

`authz_protocol_state` records which API protocols have actually started. An
existing installation does not acquire a protocol-1 record merely by migrating.
The historical claims-gate revision remains in the migration chain, but does
not require a development image to have run.

Use the normal deployment update for init, API, client, and executors. Preserve
the database, object storage, platform secret, and any legacy broker until its
recovery completes. Scheduler retirement and additional workers are separate,
optional operations. See [Upgrading From 1.4.1 to 2.0](stable-2-upgrade.md).

The authority-version migration takes bounded table locks. A lock timeout rolls
back the migration transaction; identify the long transaction before retrying.
Protocol records are observational evidence, not a substitute for checking the
images actually serving traffic. Do not roll back to a human-token validator
that could accept credentials revoked by 2.0; use a forward repair build.

## Refresh challenge

A token that a refresh can fix is refused with:

```text
WWW-Authenticate: Bearer error="invalid_token", error_description="refresh_required"
```

That is an expired access token with a refresh path (a person's token, or a
run token minted with authorization claims), and a run token minted under an
older authority epoch. The authentication dependency raises it before any
route handler or later dependency runs, so a client may refresh and replay
the request. Other 401s keep `WWW-Authenticate: Bearer`, including a run
whose execution has ended or whose service lease has lapsed (a refresh would
be refused too).

The SDK refreshes and replays only on this challenge, at most three times per
call (shared by the call's transient retries), on every transport including
streams, before any response bytes reach workflow code. The MCP bridge
re-mints in-process the same way. A call can therefore fail with 401 if more
than three authority changes land within it.

## Runtime credentials and the legacy bridge

From the second token release, runs receive an access token carrying their
authorization facts plus a run refresh token. Remote launchers report the
credential protocol they support when they connect; a launcher that reports
none (an older build) keeps receiving the older, identity-only runtime token,
and its service attempts keep the parent's token rotation. Each run's row
records which format it received (`credential_version`: 2, or NULL for the
older format).

Older-format runtime tokens resolve current authority from the database and
work only while their durable run is live. This includes the engine-token
format emitted by stable 1.4.1, which identifies an execution through
`engine_execution_id`. Its signed administrator, Solution, and repository
flags do not grant authority. Missing legacy lineage is derived from the
recorded user or the workflow's unattended identity; partially recorded lineage
is rejected. New versioned credentials never fall back to this adapter. Each acceptance logs
`authz.legacy_runtime_token_accepted` with the credential kind and expiry;
count those lines to watch the bridge drain.

### Before the release that removes the bridge

The third token release refuses to install while any live run holds
older-format credentials or any connected launcher receives them. First:

1. List what remains, as a Platform Admin:

   ```bash
   bifrost admin authz legacy-status
   ```

2. Upgrade every listed remote launcher to the current release.
3. Restart every listed service attempt through the normal service restart;
   it relaunches with new-format credentials.
4. Let listed workflow runs finish; they end at their timeout at the latest.
5. Run the command again and continue only when both lists are empty.
