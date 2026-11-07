# Optional GitHub App integration

Delivery tracks [#1267](https://github.com/gobifrost/bifrost/issues/1267), with
connections (#1268), events/recovery (#1269), then setup/health/examples (#1270).
The implementation assumes maintainer acceptance; these decisions remain
reviewable through the normal PR gates.

## Contract

- Each deployment configures its own optional GitHub.com App registration.
  The platform administrator supplies and can replace/revoke the RSA private
  key and webhook secret. Both are encrypted and write-only. No registration,
  installation, permission upgrade, ownership transfer, or production rollout
  is performed by this delivery. PAT sync remains independent.
- A Bifrost organization may bind multiple installations. Each installation
  binds to one organization, with a database uniqueness constraint. Only a
  deployment administrator can claim/reassign an installation, after GitHub
  verification. Tenant inventory requires organization reach and integration
  read permission; credential-backed refresh also requires secrets read.
- All provider calls use fixed GitHub.com endpoints, explicit timeouts, bounded
  pagination/concurrency, and sanitized errors. App JWTs never become Bifrost
  access tokens. Installation tokens remain backend-only, expire early, and are
  cached encrypted with App/binding generation and exact permissions/repository
  scope. Replica-wide locks coalesce refresh; invalidation fences late results.
  Cooldowns apply to the installation, not an individual token.
- One App-level webhook route requires strict SHA256 HMAC over bounded raw
  bytes. It checks the event/action and verified installation/repository binding
  before tenant dispatch. Unknown bindings never auto-enroll. Lifecycle events
  invalidate cached authority and inventory; reconciliation restores access only
  after a fresh provider check, never from an old webhook's assertion.
- Accepted deliveries use existing Event/EventDelivery storage and workflow
  dispatch. App/delivery identity is unique across replicas and fan-out, and raw
  bytes are retained. Duplicate requests reuse the accepted identity and resume
  pending work. Commit precedes dispatch/acknowledgement. This adds no generic
  webhook inbox, alternate workflow engine, worker, or browser polling protocol.
- Durable inventory/recovery operations use PlatformJob. Existing scheduler
  triggers enqueue bounded reconciliation. Provider failed-delivery redelivery
  uses App authority; tenant responses contain only their own health/correlation.
  Recovery checkpoints, retention limits, and gaps are explicit. No promise of
  exactly-once workflow side effects or complete history beyond GitHub retention.

## Initial capabilities

Manual configuration and read-only installation/repository inventory precede
repository, pull_request, issues, check_run/check_suite, and release events.
Metadata is read-only; pull requests, issues, checks, and contents read access
are requested only for the selected event families. Installation lifecycle is
control-plane traffic. There is no user-token flow or silent credential fallback.
Manifest onboarding, write-back, App-backed Git sync, broker/fleet clients, and
T3 changes are later work outside these three phases.

## Proof and rollout

Unit tests cover signing, permissions, raw-body HMAC, error classification,
rate scheduling, cache partitioning and refresh races. Database/endpoint tests
cover isolation, conflicting bindings, concurrent dedupe, lifecycle revocation,
commit/dispatch crash recovery, and redaction. Component/service tests and one
Playwright journey cover manual setup, health and a read-only example.
Focused tests use `./test.sh`; Python quality, client lint/types, generated
OpenAPI types, permission/DTO/MCP/export tripwires and normal merge-queue checks
remain required for their changed surfaces.

Synthetic provider fixtures prove local integration contracts, not live GitHub
qualification. A live test App/installation would need separate authorization.
Deployment target and runtime acceptance are not inferred from upstream merge.
