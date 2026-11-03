# Run credential refresh and output redaction

The genuine fixture now exercises upstream SDK refresh inside the first of two
live tenant attempts. It checks that a replacement access token differs from the
original and that the run refresh identity remains stable, then emits all three
values through print, a logging warning and a ValueError. Upstream owns issuance,
credential adoption, secret registration and output scrubbing. No SDK core or
refresh bridge changes were necessary.

CT128 command:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/refresh-redaction-v1/ct128-canonical-failure.py \
  --refresh-redaction --revision 1
```

Exit0. The real `/auth/refresh` API returned200. Canonical Failed/error and persisted
logs contain only:

```text
refresh_probe original=[REDACTED] replacement=[REDACTED] refresh=[REDACTED]
```

There are two exact persisted marker messages (print and warning) plus the
redacted ValueError traceback line. The first attempt's initial credential log
is also redacted. The workflow verifies replacement/stable-run-refresh facts
before emitting the values; no raw replacement token is required in the parent
report. The report guard now withholds JWT-shaped data in addition to all
parent-known credentials, covering a replacement unknown to the parent.

Existing checks passed: genuine SDK effects, independent peer Success78,
duplicate dispatch reuse, lost terminal ACK, owner reconnect, stale-owner
rejection, unchanged canonical state/log IDs after replay, failed access401 while
peer access200, both completed credentials401, native reservations2,2,1,0,
canonical accounting and fixture cleanup. No owned containers remained running.
Supervisor73.765s includes setup/prewarm/work/cleanup, not startup or CPU latency.
Ruff and Pyright passed for the changed fixtures.

Raw receipt with source/runtime/image/packet pins:
`../results/refresh-redaction/receipt.json`. Verify using
`node backend/verify-refresh-redaction.mjs` from the spike directory.

This is local real engine/SDK/API integration, not a mocked refresh and not
deployed Cloudflare acceptance. It tests existing scoped runtime identity, not
new persistent credentials or grants. No cloud call, runtime-setting change or
production effect. It does not establish arbitrary-code containment, general
secret non-exfiltration or physical cancellation. Admission remains disabled.
