# Table Name Uniqueness Repair

Migration `20261101_table_name_unique` restores the partial unique indexes
for ad-hoc (`solution_id IS NULL`) tables:

- one `(organization_id, name)` pair per organization;
- one global `name` where `organization_id IS NULL`.

Solution-managed tables keep their existing separate `(solution_id, name)`
unique index, so a solution table may still share a name with an ad-hoc table.

Before its duplicate check, the migration takes a PostgreSQL `SHARE` lock on
`tables` and holds it through index creation. This blocks table writes while
the check and indexes are made, so an older API image cannot introduce a
duplicate between them. If the check finds duplicate ad-hoc names, the
migration stops before changing rows or indexes and prints each scope, name,
and table ID. Resolve those records deliberately (including any document data
that must be retained), then rerun `alembic upgrade head`. The migration does
not merge, delete, rename, or otherwise alter existing table data.
