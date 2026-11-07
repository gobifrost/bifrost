# Optional infrastructure providers

Cloudflare is an optional physical provider, not a separate BiFrost platform.
Hosting, ingress, storage and execution must be independently selectable. A
Cloudflare frontend must not require a Cloudflare executor; selecting execution
must not replace PostgreSQL scheduling, authorization, attempts, results or recovery.

## Implemented companion configuration

The executor cf config now loads deployment identity through
`executor/deployment-options.mjs`. Without overrides it retains the existing
isolated development targets. An installation override must supply all four:

```text
BIFROST_CF_ACCOUNT_ID=<32 lowercase hexadecimal characters>
BIFROST_CF_EXECUTOR_NAME=<Worker name>
BIFROST_CF_API_WORKER=<API gateway Service Binding Worker name>
BIFROST_CF_API_ORIGIN=https://<trusted-api-public-origin>
```

Partial overrides fail before cf configuration is built, preventing accidental
cross-installation account/origin/binding combinations. Origins must be HTTPS
roots without credentials, query or fragment. Configuration contains no tokens.
Changing a target is operator configuration, not permission to access that target.
Existing private guard/expiry, disabled preview URLs and CPU limits are unchanged.
The canonical staging command includes the configuration helper in its source
manifest. Existing historical build receipts continue verifying their old source;
a new build is needed before deploying the changed candidate.

This is executable companion deployment configuration, **not an upstream Settings
API or production backend selector**. Public host admission still rejects the
unqualified Python profile. No implicit fallback to Linux or second queue exists.

## Shared provider contract to integrate

Use upstream's persisted placement and attempt ownership. The provider implements
the physical launch/observation/result transport boundary under that ownership;
it does not decide authorization or canonical terminal state. Runtime inventory,
dependency/source provenance, supported requirements and limits must be validated
before dispatch. Unsupported combinations fail explicitly before effects.
Uncertain invocation outcomes retain obligations until canonical reconciliation;
they must never become permission to launch another copy.

Linux remains the existing default. A Cloudflare option can become selectable for
qualified reviewed workflows only after guarded canonical engine/SDK cloud
acceptance. Arbitrary-code admission remains a separate unresolved security gate.
Existing profile/capacity code is not silently generalized by this document.

## Independent infrastructure slices

* Hosting: compiled frontend/assets provider; demonstrated experimental Worker.
* Ingress: gateway provider forwarding to configured canonical API; demonstrated
  experimental proxy. Webhook signature/dispatch parity is the next candidate.
* Execution: Dynamic Workers/Pyodide physical adapter; real SDK execution exists,
  but latest combined canonical/deployed acceptance remains incomplete.
* Storage: R2 should first qualify through the existing S3-compatible contract;
  do not add another storage API merely because it is Cloudflare.

These are separate choices and acceptance scopes. Database authority, scheduling,
durable recovery and persistent services stay upstream; no D1/Queues/DO substitute
is introduced. New production configuration surfaces require normal review and
their own contract tests, as the optional Azure/S3 approach does.

Verification: 18 focused Node tests pass across deployment options, admission and
staging rejection. No new account resources, credentials or deployment were used.
