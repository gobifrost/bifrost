# Builder sandbox execution

**Status:** implementation contract  
**Updated:** 2026-08-07

## Decision

Builder turns and application builds are durable `PlatformJob` operations that
execute through a provider-neutral sandbox interface. Cloudflare Workers,
Workflows, and Sandbox Containers are the recommended production provider. A
local provider is explicit and optional for development or deliberate
self-hosting. There is no automatic provider fallback.

The Cloudflare path adds no long-running Bifrost container. The existing
scheduler claims the job, starts one Cloudflare Workflow instance, records its
external identity, changes the job to `waiting`, and releases its runner. The
Workflow starts a short-lived sandbox container from the version-matched
Bifrost runner image. The sandbox receives only a job envelope and a
short-lived capability bound to that exact job.

The browser-facing application route remains `/apps/{slug}`. Sandbox execution
does not require another Bifrost public port, DNS record, or application
origin.

## Components and trust boundaries

| Component | Trust | Credentials | Responsibility |
| --- | --- | --- | --- |
| Bifrost API/scheduler | trusted control plane | database, object storage, AI provider, encrypted Cloudflare token | authorize, stage, enqueue, meter, dispatch, validate, finalize |
| Cloudflare Workflow | untrusted job transport | one job capability only | create one sandbox, run the fixed harness, report terminal state |
| Sandbox container | untrusted execution plane | one job capability only | edit/build staged source and return bounded artifacts |
| Generated app iframe | untrusted browser runtime | app-scoped runtime capability | use only declared runtime SDK operations |

The Cloudflare API token and the configured AI-provider key never enter the
Workflow payload or sandbox. The sandbox calls Bifrost's job-bound AI proxy;
Bifrost performs the upstream model call and records usage against the job,
user, organization, and Solution.

## Canonical job envelope

Every provider receives the same serialized envelope:

```json
{
  "schema_version": 1,
  "job_id": "uuid",
  "job_type": "solution.build | solution.builder.turn",
  "callback_base_url": "https://bifrost.example.com",
  "capability": "short-lived JWT",
  "input_sha256": "sha256",
  "timeout_seconds": 900
}
```

The capability has an actor contract, not a human authorization scope. It is
accepted only by `/api/internal/sandbox/jobs/{job_id}/*`, binds the exact job,
job type, current dispatch attempt, and allowed operations, and expires at the
job deadline. It is not a normal Bifrost SDK token and cannot be assigned to a
role.

The callback API is provider-independent:

- `GET .../input` streams the staged immutable input;
- `POST .../progress` records a bounded phase and progress value;
- `PUT .../artifacts/{path}` accepts bounded build artifacts;
- `PUT .../output` accepts one bounded turn workspace archive;
- `POST .../llm/chat/completions` proxies the configured Builder model and
  meters usage;
- `GET .../cancelled` lets the harness stop cooperatively;
- `POST .../complete` validates and atomically finalizes the projection and
  parent PlatformJob.

## PlatformJob lifecycle

1. The API validates authorization and readiness, stages immutable input, and
   creates the feature projection and `PlatformJob` in one transaction.
2. A scheduler runner, or a trusted parent deploy using the same fenced
   external-dispatch helper, changes `queued` to `running` and obtains a lease.
3. The selected provider starts the external run with `job_id` as its
   idempotency identity.
4. Bifrost atomically records `external_provider`, `external_run_id`, and
   `external_started_at`, changes the job to `waiting`, and clears the local
   runner lease.
5. `waiting` jobs continue to occupy their resource lock and handler
   concurrency allowance. They do not occupy a scheduler process.
6. Progress and completion callbacks are fenced by the job capability and
   dispatch attempt. Completion verifies hashes, paths, sizes, and current
   Solution revision before committing.
7. Cancellation marks the PlatformJob and asks the provider to terminate the
   external run. A late callback cannot revive a terminal job.

`solution.deploy` may synchronously request a child `solution.build`. The child
must be dispatched immediately through the same external-dispatch service
before the deploy begins waiting; placing only a queued child behind the same
single scheduler would deadlock. The child remains a normal PlatformJob and is
not a hidden side queue.

## Builder-turn finalization

A turn is created in `queued` state with its prompt in the PlatformJob's
encrypted payload and the project's current revision as `base_revision_id`.
The input contains that revision, the persisted conversation transcript, the
Builder Agent Skill, and a machine-readable Solution authoring contract.

On successful callback Bifrost takes the Solution write lock, verifies that
`current_revision_id` still equals the turn's base revision, validates the
returned workspace, and writes a new immutable revision only when the content
hash changed. It then appends the durable user and assistant transcript,
records tool/usage metadata, and enqueues the preview deploy. Reopening the
Builder therefore restores the full transcript and last deployed preview while
any new launch is shown as ordinary PlatformJob progress.

## Cloudflare provider

The Cloudflare implementation uses:

- a versioned, pre-bundled Worker module shipped with Bifrost;
- one Workflow class for durable execution;
- Cloudflare Sandbox's version-matched Durable Object and container binding;
- a versioned public Bifrost sandbox-runner image containing the fixed app
  build toolchain and coding harness;
- the Cloudflare REST API to create Workflow instances using
  `job_id` as `instance_id`.

The runner image is an execution artifact, not a new Bifrost service. It is
published by Bifrost's release pipeline and pulled by Cloudflare on demand.
Production Bifrost still runs the existing API, client, scheduler, workflow
worker, and data services only.

### Hoster setup

The administrator opens **Settings > Builder runtime** and completes a guarded
wizard:

1. Configure and test the Bifrost AI provider and optional Builder model.
2. Paste a Cloudflare API token. Bifrost verifies it and discovers accessible
   accounts; a choice appears only when the token reaches more than one.
3. Confirm the callback address. It defaults to the current Bifrost browser
   origin and requires no second hostname. Bifrost verifies HTTPS in
   production and performs an external callback probe.
4. Select capacity and budget defaults, then choose **Provision runtime**.
   Provisioning is itself a PlatformJob and uploads/updates the versioned
   Worker, Workflow, and Sandbox configuration.
5. The wizard shows live checks for credentials, account, Worker deployment,
   Workflow dispatch, sandbox boot, callback reachability, artifact storage,
   scheduler health, and a minimal end-to-end build.
6. **Enable Builder** becomes available only when every blocking check is
   green. Ordinary users do not see Build before that; administrators see the
   readiness card and direct links to the missing setting.

The token is encrypted at rest and masked after save. Re-provisioning is
idempotent and uses stable Bifrost-owned script/workflow names. Disabling
Builder stops new jobs without destroying saved work. Disconnecting a provider
is a separate explicit operation and never switches to local execution.

For local development, the explicit local provider uses the same callback and
runner contracts. It may use an opt-in local runner profile or a developer-only
sandbox process, but the UI must label the weaker isolation and refuse that
mode in production. Cloudflare cannot call an unexposed `localhost`; local
development therefore does not pretend to be a Cloudflare connectivity test.

## Capacity, cost, and tenancy

Every job records provider identity, model usage, elapsed time, estimated
container CPU/memory/disk consumption, and the owning user, organization, and
Solution. Limits are checked before dispatch and enforced again by the
job-bound AI proxy. The Builder UI shows consumed/remaining percentages and an
estimated dollar amount; administrators can filter and aggregate by customer,
user, Solution, provider, and job type.

Provider-wide access does not widen the default catalog. Builders see their
own and explicitly shared work. Operators and administrators deliberately open
**All work** or **Needs review**, then filter by organization and owner. Every
cross-tenant read or mutation remains authorized and audited.

## Cloudflare source references

- [Sandbox overview](https://developers.cloudflare.com/sandbox/)
- [Sandbox Docker images](https://developers.cloudflare.com/sandbox/configuration/dockerfile/)
- [Workflows triggers](https://developers.cloudflare.com/workflows/build/trigger-workflows/)
- [Workflow instance API](https://developers.cloudflare.com/api/resources/workflows/subresources/instances/methods/create/)
- [Worker module upload API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/update/)
- [Workers multipart metadata](https://developers.cloudflare.com/workers/configuration/multipart-upload-metadata/)
- [Workers and Containers configuration](https://developers.cloudflare.com/workers/wrangler/configuration/)

