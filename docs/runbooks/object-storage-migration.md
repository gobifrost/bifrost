# Object storage provider migration (S3 to Azure Blob)

One-time, stop-the-world cutover for the first Blob adoption. The source
bucket is never modified, so flipping the provider back is the rollback.
Do not use a raw CLI copy: only the platform adapter applies the Azure
metadata envelope and issues fresh concurrency versions.

## Quiesce

1. Stop API, worker and scheduler so no upload, move, Git sync or checkpoint
   is in flight. A move marker left by an interrupted move resolves itself
   after cutover by re-copying; do not hand-edit destination metadata.
2. Record the source object count for the final reconciliation.

## Prepare the destination

3. Create the destination container out of band. Bifrost never provisions
   storage; the migration refuses to run against a missing namespace, and it
   refuses a non-empty container unless the run is a resume.
4. Export both credential sets in one shell. The script reads S3 settings
   for the source and `BIFROST_AZURE_BLOB_*` for the destination; the
   installation's `BIFROST_OBJECT_STORAGE_PROVIDER` stays `s3` until cutover.

## Trial slice, then the full copy

5. Trial one prefix first (for example `--prefix _apps/`), then the full run:
   `BIFROST_MIGRATE_OBJECT_STORAGE_ACK=1 python scripts/migrate_object_storage.py`
   from the API image (`docker compose run --rm --no-deps api ...`), which
   carries both the S3 and Azure SDKs. Add `--verify-digests` for a full
   readback comparison on a small installation; the default check compares
   size, metadata and content properties for every key.
6. The script skips keys that already match for a crash resume (pass
   `--allow-nonempty-dest`). With `--verify-digests`, matching-size existing
   objects are read and compared too; different bytes are copied again, and
   concurrent destination changes fail verification. It reports keys whose HTTP Expires property
   was dropped — Azure has no native Expires — so retention can be
   re-applied at the destination. Any `FAILED` line aborts the cutover:
   fix the cause and resume; a zero exit with no failures is the only
   accepted result.

## Cutover and verification

7. Set `BIFROST_OBJECT_STORAGE_PROVIDER=azure_blob` with the Blob settings
   and restart API, worker and scheduler.
8. Run one Git Fetch plus a workspace sync and confirm `/health/detailed`
   reports storage reachable. Durable Git baselines and receipts reference
   the old provider's versions; the first access re-establishes them from
   the migrated bytes (full re-sync, not silent divergence), and stale move
   markers re-resolve on next access.
9. Keep the source bucket until the installation has run normally, then
   retire it through the normal data-retention process.
