# Topic Events

Topic events are emitted programmatically — by the platform itself or by user workflows via the `bifrost.events` SDK. Source type: `topic`. Each topic maps to an `EventSource` row with `source_type='topic'` and an `event_type` column holding the topic string.

## Topic naming

Topics are lowercase, dot-separated strings validated against `^[a-z0-9_.]+$`. They must contain at least one dot and be at most 100 characters. Examples: `user.invited`, `acme.deal_won`, `ticket.status_changed`.

## Creating a topic source

In the Events UI (platform admin only):
1. Create Event Source → Source Type: **Topic**
2. Select a known topic from the registry or enter a custom topic.
3. Optionally assign an Organization — events emitted server-side default to this org.

Via CLI:
```bash
bifrost events create-source --name "User Invited" --source-type topic --event-type user.invited
```

## Subscribing workflows

Subscriptions on a topic source fire for every event emitted to that topic, regardless of scope. All active subscriptions on the matching source are triggered.

```bash
bifrost events subscribe <source-id> --workflow send-invite-email
```

## Emitting events from workflows (SDK)

```python
from bifrost import events

result = await events.emit(
    "acme.deal_won",
    {"deal_id": "...", "amount": 50000},
)
print(result["subscribers_notified"])
```

`scope` defaults to the current execution's org. Pass an org UUID to override:
```python
await events.emit("acme.deal_won", data, scope="org-uuid-here")
```

## context.event

When a workflow is triggered by a topic event, `context.event` is populated:

```python
context.event.id             # str — UUID of the Event row
context.event.type           # str — topic string, e.g. "user.invited"
context.event.data           # dict — the payload passed to emit()
context.event.organization_id  # str | None — org stamped on the event at emit time
context.event.received_at    # str — ISO-8601 timestamp
```

The payload is also available under `context.parameters["_event"]["body"]` for input_mapping templates.

---

## user.invited

**Topic:** `user.invited`
**Emitted by:** Bifrost platform — users router

### When emitted

- A platform admin calls `POST /api/users` with `invite: true` (and `trigger_automation` is `true` or omitted).
- A platform admin calls `POST /api/users/{id}/invite/resend`.

**Not emitted on:**
- `POST /api/users/{id}/invite/regenerate` — returns the link to the admin for manual delivery; no automation triggered.
- `POST /api/users` with `invite: true, trigger_automation: false` — invite record is created but no event fires.

### Payload (context.event.data)

```jsonc
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "alice@example.com",
  "name": "Alice",
  "registration_url": "https://app/accept-invite?token=...",
  "expires_at": "2026-05-26T23:14:08+00:00",
  "invited_by": {
    "user_id": "...",
    "email": "admin@example.com",
    "name": "Admin Name"
  },
  "reason": "created | resent"
}
```

### Organization scope

The event is stamped with the invited user's organization. Global users have `organization_id = null`.

### Example workflow

```python
async def send_invite_email(context):
    event = context.event
    data = event.data
    url = data["registration_url"]
    email = data["email"]
    # ... send email
```

Or via input_mapping template:
```yaml
registration_url: "{{ _event.body.registration_url }}"
```

---

## user.created

**Topic:** `user.created`
**Emitted by:** Bifrost platform

Fired once a new user account is committed. `creation.method` says how:

| Method | When |
|--------|------|
| `admin` | An administrator calls `POST /api/users`. The actor is the administrator. |
| `sso_provisioning` | A first SSO sign-in creates the account by email-domain match. `creation.provider` is `microsoft`, `google` or `oidc`. The actor is the new user. |
| `registration` | `POST /auth/register` or first-user passkey setup creates the account. The actor is the new user. |
| `bootstrap` | The configured default user is created at startup. The actor is Bifrost. |

Accepting an invite completes an account an administrator already created, so it
does not fire `user.created` again.

```jsonc
{
  "schema_version": 1,
  "occurred_at": "2026-11-16T12:34:56+00:00",
  "organization": {"id": "…", "name": null},
  "actor": {"type": "user", "id": "…", "email": "alice@example.com", "name": "Alice"},
  "user": {"id": "…", "email": "alice@example.com", "name": "Alice", "organization_id": "…"},
  "creation": {"method": "sso_provisioning", "provider": "microsoft"}
}
```

## user.signed_in

**Topic:** `user.signed_in`
**Emitted by:** Bifrost platform

Fired after Bifrost issues a session to a user who signed in with a password
(after MFA, when MFA applies), a passkey, or SSO. `sign_in.method` is
`password`, `passkey` or `sso`; `sign_in.provider` is `microsoft`, `google` or
`oidc` for SSO and `null` otherwise. A first SSO sign-in that provisions the
account fires `user.created` and then `user.signed_in`.

It does not fire for token refresh, or when an already signed-in user
authorizes the CLI (device code) or an MCP client.

For SSO, `identity` carries the identity provider's id_token claims that
Bifrost verified during sign-in. Bifrost removes token-binding values
(`nonce`, `at_hash`, `c_hash`, `s_hash`), the IdP session id (`sid`) and
Microsoft's opaque `aio`, `uti` and `rh`, and passes every other verified claim
through, so tenant-specific claims such as `roles` reach the workflow. Access,
refresh and id tokens and the provider's unverified userinfo are never
included. `identity` is `null` for password and passkey sign-ins.

`identity.groups` is the `groups` claim when the provider sent a list. Entra
replaces the list with `_claim_names` / `hasgroups` when a user belongs to too
many groups (groups overage). Then `identity.groups` is `null`,
`identity.groups_overage` is `true`, and the workflow must read the user's
groups from Microsoft Graph.

```jsonc
{
  "schema_version": 1,
  "occurred_at": "2026-11-16T12:34:56+00:00",
  "organization": {"id": "…", "name": null},
  "actor": {"type": "user", "id": "…", "email": "alice@example.com", "name": "Alice"},
  "user": {"id": "…", "email": "alice@example.com", "name": "Alice", "organization_id": "…"},
  "sign_in": {"method": "sso", "provider": "microsoft"},
  "identity": {
    "claims": {"iss": "…", "sub": "…", "tid": "…", "oid": "…", "groups": ["…"]},
    "groups": ["…"],
    "groups_overage": false
  }
}
```

### Syncing identity-provider groups to roles

Subscribe a workflow to `user.signed_in` and map `identity.groups` to Bifrost
roles. Delivery is asynchronous: the sign-in completes before the workflow
runs, and the workflow cannot block it. A workflow that deactivates the user
cuts them off on their next request, because every request checks that the
user is active.

Both topics are stamped with the user's organization. Emission is best effort:
a failure to record the event is logged and never fails the sign-in or the
account creation.

---

## workflow.completed

**Topic:** `workflow.completed`
**Emitted by:** Workflow executor

This event is emitted only after a workflow result is durably recorded with
status `Success`. It is emitted after a synchronous caller has been notified.
`CompletedWithErrors`, failed, timed-out, cancelled, and fenced stale results
do not emit it.

The payload contains the workflow and execution identifiers, `Success` status,
the workflow attempt (`1` of `1` because workflow execution has no retry
loop), persisted execution timing, and trigger metadata. It never includes
workflow inputs, results, or variables.

Direct self-subscriptions are recorded as skipped: a `workflow.completed`
event for workflow A cannot trigger A again. Completion chains such as A → B →
C remain valid. Configure subscription filters to bound cycles spanning
multiple workflows.

Built-in event emission is best effort, like `workflow.failed`. A process crash
after the execution commit and before emission can leave a successful run
without a corresponding completion event.

## Workflow Failure Topics

`workflow.failed` reports a failed workflow execution attempt.
`workflow.retry_exhausted` reports a failure with no attempts remaining. They
share the common event envelope, workflow and execution identifiers, trigger
metadata, and an `error` object with `type`, `code`, `message`, and `retryable`.

Subscribe to `workflow.completed` for successful runs and `workflow.failed`
for failures. Fetch an execution's result by its ID through the execution API;
the completion event carries no result data or additional result access.
