# Browser Setup Diagnostic — October 31, 2026

The reduced login diagnostic completed with four successful contexts and one
failure before authentication. It produced no workflow action-to-render samples.

| Declared Context | Result | Failed Asset Requests | JavaScript Page Errors |
|---|---|---:|---:|
| First native context | Email field never appeared; remained on `/login`; no auth response | 30, all `ERR_NETWORK_CHANGED` | 0 |
| Instrumented context, next | Login and identity read both HTTP 200 | 0 | 0 |
| Three subsequent native contexts together | All three signed in successfully | 0 | 0 |

The failed native page received only 24 script responses; each successful page
received 56. This identifies an asset-loading interruption before login for
that case. The observations do not identify the network event that caused it.
The fixed order and small sample do not establish a failure probability or an
advantage from instrumentation. Chromium's Linux notifier observes IP and link
changes, but those kernel events were not captured in this diagnostic.
[Source: Chromium Linux Network Notifier](https://github.com/chromium/chromium/blob/main/net/base/network_change_notifier_linux.cc).

The test used the unchanged production client image, the frozen `7288a2ba3`
harness, the `d2f25fc48` candidate API, and the prepared Playwright runner with
1 CPU / 1 GiB. It used the M smoke allocation without the workflow catalog or
an app build. TLS preparation and credential handling matched the browser
benchmark; no real provider or customer workflow ran. An unauthenticated
refresh returning HTTP 401 in the successful contexts is expected; their
subsequent password login and identity read succeeded.

The full-seed browser R5 attempt separately failed waiting for the email field
and retained no action samples. Its earlier diagnostics did not cover that
stage, so this reduced result must not be presented as proof of R5's exact
cause or of a production authentication regression. The subsequent R6 check
prepared the browser Pod's network before TLS/build work, then launched Chromium
in that existing namespace. Three authenticated sessions serve the action
matrix; twelve rapid logins would exceed the server's ten-per-minute limit.
The measured R6 result is retained below.

The private `runs/browser-login-diagnostic-20261031-r1/` directory retains the
declared source hashes, raw redacted runner output, Pod image identity and
verified namespace cleanup. Final artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `result.json` | `f280c725798f03f9da3b4308b6c64da1de5abee0e4520e23a86b28b85a580e9a` |
| `runner-raw.log` | `b5eef08ecbf1287663bc1b3f770fb98d8d907c4c352a0b9e8374a6be0d9e84e0` |
| `cleanup.json` | `c4121f6d20364d40b96b80a377ddf03b02352745f09a17ee7a51f88add83706d` |

## R6 Stock Browser Observation

The later stock R6 observation completed all 24 browser actions successfully
and validated their durable records. Cleanup verification completed at
23:26:34 UTC. This result adds a successful observation after the retained R5
and login-diagnostic failures; it does not identify the cause of either
failure, establish that a network condition was fixed, or estimate a failure
probability.

The API and worker used the exact captured production runtime source
`e611d31bb`, based on registry image digest
`sha256:a15c4761740ae3ac918c2e0280464f25756d08fa8d03485b2e155c6d387bb692`.
The cached build image added only the verified npm cache to that stock runtime;
it did not copy application or platform source from the experimental image.
The canonical build job succeeded before timing, and its policy and runtime
were restored before the browser actions. The client is a separate unchanged,
frozen benchmark image: host identity
`sha256:ebb8a6cf5cea2ae94a48a359b7244828c2f07850a41c7c662de14bb30e61f5ae`
and CRI image identity
`sha256:987345ae923ddbba9db4aa72512f8e1a48b0b6bd35a138574b2cd0df2e7059c2`.
These identities do not establish that the entire local stack was production.

The browser runner had its own 1 CPU / 1 GiB request and limit outside the
tenant M envelope. The M profile configured one warm worker and a maximum of
two; the restoration check observed one live and ready worker before timing,
but this run has no tenant resource time series from which to infer an actual
worker count throughout the observation.

Each cell contains three actions. “First load” means the first page action in
that browser context; it is not a scale-from-zero or cold-worker measure.
Action-to-render values are wall-clock durations from the action invocation to
the rendered success state, measured on one browser performance clock.

| Variant | Stage | Samples | Action-to-render min / median / max (s) | First terminal observed-to-render range (ms) |
|---|---|---:|---:|---:|
| Control | First load | 3 | 0.760 / 0.911 / 1.211 | 19.0–38.9 |
| Control | Steady | 3 | 0.798 / 1.164 / 1.327 | 19.5–29.7 |
| Same body graph | First load | 3 | 4.796 / 5.345 / 5.694 | 18.7–44.8 |
| Same body graph | Steady | 3 | 5.097 / 5.280 / 5.463 | 30.0–34.6 |
| Shallow list | First load | 3 | 4.513 / 4.713 / 5.113 | 25.4–33.5 |
| Shallow list | Steady | 3 | 3.498 / 3.581 / 3.595 | 20.5–32.8 |
| Detail hydration | First load | 3 | 4.413 / 4.529 / 4.787 | 21.3–25.9 |
| Detail hydration | Steady | 3 | 4.498 / 4.865 / 5.013 | 20.3–32.2 |

All 24 actions first observed their terminal state on `streamed_ws`. This is a
first-terminal-path label, not a mutually exclusive SDK branch: native ready
and terminal GET checks may overlap. Across all samples, first terminal
observed-to-render was 18.7–44.8 ms on that same browser clock. It does not
bound database completion-to-terminal delivery. The original 33-second outlier
remains unattributed.

The private R6 evidence retains the raw 24-action browser stream and durable
records. Its result artifact is
`runs/interactive-stock-20261031-r6/result.json`
(`f55f47bffa9b9eb8da3301fbb50c02b85529e1305eee7d29e004bad4afd747a1`).
The distinct setup-argument validation failure remains in
`notes/browser-stock-r6-invocation-disposition.json`; no workload ran for that
attempt. With three samples per cell, this observation supports the reported
wall-clock medians only; it makes no capacity, p95, reliability, or cloud claim.
