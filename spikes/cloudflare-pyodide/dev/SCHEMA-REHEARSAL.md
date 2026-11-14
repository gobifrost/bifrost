# Schema-only upgrade rehearsal

CT128 ran the real Alembic chain from `20261031_app_sdk_binding` to
`20261101_table_name_unique` on an empty clone of the existing dev schema.
All four required worker columns and three expected indexes exist afterward.
The disposable database was dropped and read back absent; the existing dev
database remained at `20261031_app_sdk_binding`.

Source: `1d33150eb376b71f8a81b427920eda1b0447f829`. Subsequent
`git diff --exit-code HEAD -- api/alembic` returned zero on that checkout.
The existing dev API image supplied dependencies; copied unmodified pinned
Alembic source supplied migrations. This is not a matched API qualification.
No private rows or credentials were copied. The clone used
`pg_dump --schema-only --no-owner --no-privileges`; the prior revision was
inserted explicitly into the empty version table.

Completed command (do not rerun the same packet):

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/ct128-schema-rehearsal-v1.py
node spikes/cloudflare-pyodide/dev/verify-schema-rehearsal.mjs
```

Guest command exit 0; full lifecycle 4.175 seconds. Raw receipt is
`schema-rehearsal-v1.json`; guest receipt is under the owned
`runs/schema-rehearsal-v1`. Task-owned copied migration code remains in the API
container at `/tmp/pyodide-ddf316c1-schema-v1`. No service restart or source mount
changed. Subprocesses have explicit timeouts; no heavy suite was launched.
Both controllers passed Ruff in a network-disabled 0.5 CPU/256 MiB container.

Before starting, four completed providers were read back stopped and their
receipts checked. Exact runtime copies from `canonical-cancel-v4`,
`canonical-cancel-v7`, `canonical-https-v6`, `canonical-https-v7` were compressed
under owned `inbox/<fixture>/project/backend` paths. Each
`generated-runtime.mjs.gz` is 4,395,547 bytes, SHA256
`03b529dc4ff91fa53f0683b9c4685444f3468d2dfaf78dd5d9fed29cb9dc24f9`.
Decompressed SHA256 was verified as
`61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731`
before each loose copy was removed. Restore into a new reproduction packet.
Disk free rose from 8,186 to 8,227 MiB; the 8,192 MiB guard was retained.
No volumes, uncertain locks, active inputs or resource allocations changed.

V2 (`ct128-schema-rehearsal-v2.py --duplicate-probe`) completed in 9.743 seconds,
exit 0. Two synthetic global table names caused the actual migration to refuse;
both rows and the prior revision were preserved, with neither unique index
partially created. Renaming the synthetic duplicate allowed upgrade; both rows
survived. The disposable database was removed and original dev revision unchanged.
Raw receipt: `schema-rehearsal-v2.json`. The executed script had one unused local
variable subsequently removed; no behavioral change or repeat run was needed.

Next: matched API qualification.
Empty-schema success does not prove existing-data upgrade safety. No original
dev upgrade, cloud invocation, public admission or CPU-stop acceptance is claimed.
