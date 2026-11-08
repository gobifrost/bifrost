# Webhook delivery identity

The Generic Webhook adapter can opt into source-scoped delivery identity with
`delivery_id_header`, for example `X-GitHub-Delivery`. A signing secret is required.
HMAC verification runs before identity admission. The header is an idempotency key,
not another authentication mechanism; HMAC authenticates the body, not this header.

Concurrent requests with the same source, delivery ID, raw body and event type reuse
the retained Event and its existing EventDelivery/run identities. A repeated ID with
a different body or event type returns 409. Missing or malformed IDs return 400;
invalid signatures return 401. Sources without this option keep their existing behavior.
Existing per-source rate limits continue to apply, including to duplicate requests.

Admission reuses the existing partial unique index on `Event.occurrence_key` with a
`webhook:<source UUID>:<delivery ID SHA-256>` namespace. A PostgreSQL upsert locks the
Event through subscription staging, run acceptance and commit. It never replaces the
original payload. The normal post-commit handoff and pending-delivery recovery remain
the execution path. Failed admission rolls back before returning an error, so a partial
Event cannot claim an identity without its fan-out.

Deduplication lasts only while that Event is retained. Retention or explicit source
deletion removes its identity; a later replay may create new work. Packages requiring
a minimum replay window must validate deployment retention before enabling ingress.
This option does not provide permanent tombstones or exactly-once external side effects.

For example, an operator-owned integration package can configure a private signed
source, verify provider-to-tenant bindings in its workflow, and then route only approved
events to tenant consumers. Provider authentication, binding policy, quota control,
recovery/redelivery requests and the package's own safe downstream retries remain
package concerns. This change adds no provider-specific platform integration.
