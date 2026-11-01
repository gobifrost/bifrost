# PR 1161 data-flow review

## Current-head follow-up at f7deaf162

The check-run annotations were read directly on 2026-10-31 (check
124706101545). The eight HTTP-to-file warnings are the intentional fixed-path,
bounded JSON evidence flows assessed below (1813–1820); they remain open for
maintainer review. Successful Python/JavaScript analysis jobs do not override
the failing alert gate. No dismissal or suppression was applied.

The canonical-local fixture's storage write was inside an assertion. Move all
effectful awaited calls outside assertions, including worker readiness, claim,
task completion and reconciliation, so optimized Python cannot omit them.
The two no-effect notes identify cancellation joins (`await heartbeat`), which
are necessary to observe task termination before releasing ownership/closing
the database. Retain those joins; they are executable asyncio synchronization,
not unused expression placeholders. No physical provider-stop claim follows
from joining this local heartbeat task.

Reviewed all 15 public PR comments on source head
`b720516c1c7f39e30d558f353bb4b144f38fafb9`. Detailed code-scanning API access
returned HTTP403. No additional scope, alternate identity, dismissal, suppression,
query exclusion or approval was requested/applied.

The published query guidance asks reviewers to inspect whether these flows are
intended: [file to HTTP](https://codeql.github.com/codeql-query-help/javascript/js-file-access-to-http/)
and [HTTP to file](https://codeql.github.com/codeql-query-help/javascript/js-http-to-file-access/).
The flows are real; this is a proposed source review, not a maintainer security
approval or a claim that the scanner must clear every finding.

## Changes

Cloud test credentials now go through one literal, approved HTTPS endpoint;
relative paths are constrained, credential format is checked, requests time out,
and redirects fail closed so the custom authentication header cannot be forwarded
to another origin. Cleanup validates the saved URL but fetches the literal target,
so an edited receipt cannot select a different network destination. Cloud mode
clears inherited local SDK identity; SDK proof explicitly pins localhost.

Diagnostic response reads are streamed with a 1 MiB byte cap (including UTF-8
validation), and oversized streams are canceled. The loopback effect observer
accepts only bounded before/after messages. Evidence is serialized as JSON to
code-selected report paths; peer data never selects a filename. These reports
are untrusted observations, not source code, package input, shell commands or
authorization. Historical raw artifacts remain unchanged.

## Each reported item

Numbers below are the upstream code-scanning alert references carried by the
public PR comments. All remaining intentional flows require maintainer review;
they have not been dismissed or self-approved.

| Alert | File / reported flow | Source disposition |
|---|---|---|
| 1808 | backend/cloud-isolation.mjs: temp secret to request | Intentional dedicated test auth; fixed-target client, bounded format, no redirects. No BiFrost token. |
| 1809 | backend/fresh-cleanup-readback.mjs: receipt URL to request | Removed destination selection from file data; exact URL assertion and literal-target client. |
| 1810 | cloud-security.mjs: temp secret to request | Intentional test auth, now same fixed-target/no-redirect client. |
| 1811 | cloud.mjs: temp secret to import probe | Intentional test auth, same constraints; inherited local SDK token cleared. |
| 1812 | cloud.mjs: temp secret to memory probe | Same fixed-target test auth; no redirects and bounded response read. |
| 1813 | backend/fresh-cleanup-readback.mjs: status to JSON report | Intentional numeric status evidence at fixed report name; management JSON is checked, never executed. |
| 1814 | backend/external-cancel.test.mjs: response/effects to JSON | Intentional cancellation evidence; response byte cap and bounded, enumerated loopback effects. |
| 1815 | backend/cloud-isolation.mjs: response to JSON | Intentional raw test observations at fixed suite report names; response streaming cap before buffering. |
| 1816 | backend/invalid-credential.test.mjs: response to JSON | Intentional real API denied-loading evidence at fixed path; bounded read, assertions still reject leaked synthetic token. |
| 1817 | backend/transport.test.mjs: response to JSON | Intentional transport-failure evidence at fixed path; bounded JSON read. |
| 1818 | cloud-security.mjs: response to JSON | Intentional security assertions/evidence at fixed path; bounded JSON read, no downloaded-code execution. |
| 1819 | cloud.mjs: import response/status to JSON | Intentional import evidence at fixed path; bounded JSON read. |
| 1820 | cloud.mjs: memory response to JSON | Intentional failure body at fixed path; bounded before its existing preview truncation. |
| 1821 | termination.mjs: interrupted response to JSON | Intentional transport-failure evidence at fixed path; bounded text read, only the named local disposable container is restarted by this opt-in script. |
| 1822 | sdk-proof.mjs: API readback to JSON | Intentional independent local SDK-write evidence at fixed path; bounded JSON read. Auth token is kept out of the artifact and forwarding is pinned to localhost. |

## Verification and limits

`node --test probe-io.test.mjs backend/module-response.test.mjs` covers fixed
destination/redirect settings, invalid path/credential rejection, UTF-8 byte
limits, stream cancellation, JSON parsing, and existing workspace integrity.
The new transport reads also exercise the existing real local API negative-token
and synchronous-egress-denial fixtures. No new cloud invocation, authentication,
SDK write or container restart is needed for this review verification.

CodeQL must analyze the new head normally. Moving an intended data flow into a
shared transport is not a suppression or evidence that it vanished. The original
CPU-termination incompatibility and incomplete executor acceptance remain open.

## Follow-up protocol findings 1823 and 1824

Both comments identify ellipsis expression bodies on the resource-observer
protocol methods in `api/src/services/execution/worker.py`. Replaced those
placeholders with `@abstractmethod` methods that raise `NotImplementedError`.
Structural implementations still supply start/finish normally, and concrete
process accounting and unknown-provider metrics are unchanged. This is a source
fix, not a suppression or alert dismissal. The existing runtime-metadata and
workspace-isolation tests cover the affected call boundary.
Verification: 7 focused tests passed through `./test.sh`; `./test.sh quality api`
passed with zero Pyright errors/warnings and Ruff clean. No cloud redeployment
or change to the historical runtime manifests was made.

## Follow-up fixture findings 1832 and 1833

Both comments point to bare `await task` expressions in the journal tests.
The awaits synchronize physical-fixture completion and propagate failures; they
must remain. Strengthened both statements to assert their exact returned values
(`{"value": 70}` and `{"status": "Success"}`), making the intended observable
contract explicit. No suppression or alert dismissal was applied.

All six journal tests passed through `./test.sh` in 6.43 seconds; raw receipt:
`results/attempt-journal-codeql-junit.xml`. The prior nine-test receipt and source
hashes remain historical evidence for the earlier implementation. This change
modifies only assertions, not the journal or platform implementation. Fresh
CodeQL analysis must establish the disposition on the new head.

## Receiver cleanup: alert 1879

The [review comment](https://github.com/gobifrost/bifrost/pull/1161#discussion_r4436825424)
flags `await receive` after cancellation in linux-control-reference.py. Awaiting
the task joins its cleanup before stopping the launcher; removing the join would
introduce a race. The fixture now explicitly gathers its single cancelled task,
inspects the outcome and rethrows non-cancellation errors. It does not discard
the cleanup operation or suppress arbitrary receiver failures.

Three real asyncio lifecycle tests passed in CT128: pending cancellation waits
for the receiver's finally block, an already completed receiver remains complete,
and ValueError propagates. The command was a bounded, network-disabled Docker
invocation of `python /qualification/test_linux_receiver_cleanup.py -v`, using
the existing test image, one CPU, 512 MiB and 64 PIDs with a 30-second timeout.
Targeted Docker Ruff passed. No database/SDK/cloud invocation or heavy laptop
Docker was needed; no running containers remained. Historical Linux receipt
verifiers pass and now bind the benchmark fixture to tested commit ca40ad458.

The detailed-alert API returned HTTP 403; no scope refresh, alternate identity
or dismissal was attempted. Public review context and exact current source
identified the statement. Fresh CodeQL is required before claiming the alert
resolved. Full backend suites were not rerun for this fixture-only cleanup.
