# 4 GB SDK Capacity Confirmation — October 31, 2026

Status: **INVALID for capacity confirmation**. This record preserves the first
full-seed 4 GB SDK correctness preflight and the subsequent fixed-rate evidence.
It does not establish a capacity upper bound or attribute the observed latency
or throughput to a component.

The `4gb-capacity` SDK-heavy run used the fake backend at an offered rate of
3.90625 requests/s. The first full reset/seed preflight submitted 127 requests;
all 127 produced correct durable results by their deadline, with no tenant
faults. That preflight is correctness evidence only.

| Phase | Window | Correct evidence | Measured result |
|---|---:|---|---|
| Full-seed preflight | 30 s | 127/127 correct durable results | Pass; no tenant faults |
| Fixed-rate confirmation | 958.464 s | 3,735/3,735 arrivals correct within the 60 s deadline | 3,533 correct terminals in the measured window; 3.686106/s event rate |

The confirmation offered 3.90625/s and recorded a 0.945917 completion/arrival
ratio. Queue p95 was 37.379951 s and end-to-end p95 was 38.264256 s. The 202
correct terminals outside the fixed event window were reconciled through drain;
they were not missing or failed arrivals. Tenant fault counters remained zero
for duplicate executions, evictions, OOM kills, quota denials, and restarts.

The host validity gate made the confirmation invalid: external CPU exceeded 1.5
cores in 36 of 189 intervals, and external disk I/O exceeded its allowed share
of the window. The artifact also records possible external memory pressure as
unproven. These host conditions prevent a capacity conclusion.

Both worker pods remained present in the aggregate samples. Settled process
private-memory observations (`private_mib`) were 267.7 MiB and 268.0 MiB;
these are not whole-Pod working sets or allocation limits. The collected
evidence does not attribute individual
executions or latency to a worker process, so it cannot support a
per-process-causality claim.

The [scalar baseline](baselines/sdk-confirmation-2026-10-31-invalid.json)
preserves exact rates, latency intervals, fault counts and image identities for
future comparisons, with capacity certification explicitly false.

## Provenance

| Item | Identity |
|---|---|
| Harness | `963b328339d96d44b74547505da8731a6c63d0da` |
| Tenant API/worker image | `bifrost-api:kind-a74e45295998-tablefix-d2f25fc48` — host ID `sha256:fac2a947ca81c8572c83ee6ece92384ccf39f8f4abd657646a8f69b2ba8042a8`; CRI config `sha256:c6755075cd4117a161fd926b436a8874ac65ca6f9b27b57ddcfb748b0f3102a4` |
| Declaration SHA-256 | `7e67269db31f86f65dbf6fb4ca4a39ffe21fe569b3244338c8250b9e33871108` |
| Raw result SHA-256 | `194c07260f558b6cc1667d94bd167e94f84fda3cb169037f63cb45ffcbecabd9` |

The raw result is retained at
`/home/jack/bifrost-bench-evidence/runs/capacity-completion-matrix-20261031-r4/4gb-capacity/sdk-confirmation/result.json`.
The matching declaration is
`/home/jack/bifrost-bench-evidence/runs/capacity-completion-matrix-20261031-r4/declaration.json`.
