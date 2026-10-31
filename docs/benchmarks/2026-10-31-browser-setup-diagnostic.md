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
cause or of a production authentication regression. The next declared check
prepares the browser Pod's network before TLS/build work, then launches Chromium
in that existing namespace. Three authenticated sessions serve the action
matrix; twelve rapid logins would exceed the server's ten-per-minute limit.
These preparation changes require a new measured browser result.

The private `runs/browser-login-diagnostic-20261031-r1/` directory retains the
declared source hashes, raw redacted runner output, Pod image identity and
verified namespace cleanup. Final artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `result.json` | `f280c725798f03f9da3b4308b6c64da1de5abee0e4520e23a86b28b85a580e9a` |
| `runner-raw.log` | `b5eef08ecbf1287663bc1b3f770fb98d8d907c4c352a0b9e8374a6be0d9e84e0` |
| `cleanup.json` | `c4121f6d20364d40b96b80a377ddf03b02352745f09a17ee7a51f88add83706d` |
