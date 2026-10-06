# Product Updates Production Contract (Follow-On Slice)

This branch implements a development-only preview and repository toolchain. It adds no product database tables, REST endpoints, runtime seeding, polling, or announcement delivery.

## Initialization and Authorization Evidence

`api/src/main.py::app_lifespan` initializes the database, registers shared entity hooks, creates the configured debug admin, and idempotently seeds built-in policies through a service transaction. A follow-on seed service can use this existing lifecycle; a simple bundled feed requires no PlatformJob. Migrations remain the schema owner. Multiple API replicas must safely execute the same seed transaction.

`api/src/core/auth.py::RequirePlatformAdmin` / `CurrentSuperuser` currently route through the platform-admin dependency. Use the same admin-only boundary for feed and receipt endpoints, plus a canonical operation/access-catalog entry. Provider-organization membership alone does not grant feed access. Receipts belong to the authenticated admin; callers cannot supply a different admin id. New MCP surfaces, if justified, use thin HTTP wrappers.

## Proposed Data and Seed Transaction

- Entry key is the permanent UUID; content stores revision, approved Markdown, dates, source/credit metadata, and assets identified by bundle manifest hash.
- Upsert is idempotent on `(entry_uuid, revision)`. Only approved entries seed. A lower revision cannot overwrite a retained higher approved revision. Same UUID/revision with different content is an integrity error, not last-writer-wins.
- Receipt key is `(admin_id, entry_uuid)` with acknowledgement timestamp. An entry revision upsert never touches receipts. A genuinely new capability or corrective announcement receives a new UUID.
- Fetching alone does not acknowledge entries. Rendering the modal or history automatically acknowledges the batch presented by that surface; the user has no mark-read chore. A receipt means presented, not proof of reading or understanding. The receipt endpoint receives the presented UUID list and validates every UUID against the running bundle before writing. Duplicate ids are harmless; invalid/ineligible ids fail validation.
- Independent admins maintain independent receipts. Dev → candidate → stable transitions retain the UUID and receipt. Logout, profile change, or another browser tab cannot transfer receipt ownership.

## Running Build Is the Visibility Boundary

Feed queries intersect retained database entries with the immutable running bundle's applicable UUID list. Database retention and publication time never widen this set. Source PR/commit inclusion is proven at build time by ancestry; all declared prerequisites must land. A staged entry needs explicit approved eligibility, not a version/date guess. `/api/version` currently lacks a commit/channel; leave its existing semantics alone.

Rollback must remain coherent: serve the immutable Markdown revision and local assets pinned by the running bundle, even when the database retains a newer approved revision. The database can retain newer content, but an older runtime cannot render it with absent assets. Return bundle identity and content revision for cache isolation. An empty applicable set is a valid feed.

## Proposed Transport

The mock adapter defines list and mark-read boundaries; implementation can map these to short request-scoped admin endpoints. No background worker or browser polling loop is needed. If receipt changes are later broadcast, use the existing user notification/WebSocket transport and authenticated admin channel. Bundle delivery remains locally available offline; receipt writes can show an honest connection failure instead of pretending acknowledgement persisted.

## Acceptance Evidence Required Later

A real API/database slice needs transaction tests for repeat/concurrent seed, older-revision rejection, same-revision integrity, and receipt preservation; endpoint tests for admin-only read/write and authenticated receipt ownership; a rollback test against newer retained rows; and a two-admin browser happy path. Mock local storage in this branch proves preview interactions only and is not evidence of production persistence.

## Future Publication Consumer

Approved publication events can feed Discord independently of software tags. Define a durable publication identity and destination-specific receipt so repeated delivery cannot repost the same revision. Consumer invocation and live posting remain separately authorized work. The preview never sends Discord messages and no private invite is bundled.
