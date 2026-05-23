# Issue 287 Readiness Probe Profiling

Purpose: reproduce the profile reported in issue #287 and verify that the
readiness fix removes repeated S3/Redis client construction before shipping.

## Issue Signal

The latest issue comment reports that a prod py-spy capture taken while
hammering `/api/workflows/execute` had this shape:

| Frame | Sample presence |
|---|---:|
| `_checked_component` | 48.4% |
| `check_s3` | 46.4% |
| `head_bucket` | 38.9% |
| `botocore/loaders load_data_with_path` | 25.4% |
| `aiobotocore _create_client` | 22.8% |
| `json.raw_decode` | 22.8% |

The important part is not S3 I/O itself. The expensive shape is
`check_s3 -> get_session -> create_client -> botocore loader -> JSON decode`,
which means the health probe is rebuilding the S3 client and rereading
botocore metadata on each readiness check.

## Local Reproduction

Baseline was captured from `origin/main` at `3a086b96` in worktree:

```bash
/home/jack/GitHub/bifrost/.worktrees/287-health-profile-main
```

Patched was captured from branch `287-data-provider-overhead` after warming the
readiness endpoint once.

Both captures sampled the API worker while hammering `/health/ready`:

```bash
sudo -n /tmp/bifrost-pyspy/bin/py-spy record \
  --pid "$worker_pid" \
  --duration 15 \
  --rate 20 \
  --nonblocking \
  --format raw \
  --output /tmp/bifrost-287-profile/<label>.raw
```

## Results

| Frame | `origin/main` | patched |
|---|---:|---:|
| `_checked_component` | 265 / 273 (97.1%) | 58 / 186 (31.2%) |
| `check_s3` | 265 / 273 (97.1%) | 28 / 186 (15.1%) |
| `head_bucket` | 264 / 273 (96.7%) | 27 / 186 (14.5%) |
| `aiobotocore get_session` | 52 / 273 (19.0%) | 0 / 186 (0.0%) |
| `aiobotocore _create_client` | 197 / 273 (72.2%) | 0 / 186 (0.0%) |
| `botocore load_data_with_path` | 134 / 273 (49.1%) | 0 / 186 (0.0%) |
| `json raw_decode` | 123 / 273 (45.1%) | 0 / 186 (0.0%) |
| `check_redis` | 0 / 273 (0.0%) | 4 / 186 (2.2%) |
| `redis from_url` | 0 / 273 (0.0%) | 0 / 186 (0.0%) |
| `redis make_connection` | 0 / 273 (0.0%) | 0 / 186 (0.0%) |
| `check_rabbitmq` | 0 / 273 (0.0%) | 20 / 186 (10.8%) |

Artifacts:

| Artifact | Path |
|---|---|
| Baseline raw | `/tmp/bifrost-287-profile/baseline.raw` |
| Baseline flamegraph | `/tmp/bifrost-287-profile/baseline.svg` |
| Patched raw | `/tmp/bifrost-287-profile/patched-clients.raw` |
| Patched flamegraph | `/tmp/bifrost-287-profile/patched-clients.svg` |

## Pass Criteria

- Readiness still checks S3 with `head_bucket`.
- Repeated readiness checks reuse the S3 health client.
- Repeated readiness checks reuse the Redis health client.
- Any cached health client is closed on app shutdown.
- A failed S3/Redis health check discards the cached client so the next check can
  reconnect.
- Changing S3 settings closes the old S3 client and creates a new one.
- The patched flamegraph has no `aiobotocore _create_client`,
  `botocore load_data_with_path`, or `json raw_decode` frames under readiness.

## Verification

Commands run from `/home/jack/GitHub/bifrost/.worktrees/287-data-provider-overhead`:

```bash
cd api && ruff check .
cd api && pyright
./test.sh tests/unit/routers/test_health.py -v
./test.sh
```

Observed results:

- `ruff check .`: passed.
- `pyright`: 0 errors, 0 warnings, 0 informations.
- `./test.sh tests/unit/routers/test_health.py -v`: 16 passed.
- `./test.sh`: 3939 passed, 230 warnings.
