# Internal Events

Internal events are emitted by the Bifrost platform itself (source type `internal`). They use the same subscription and delivery infrastructure as webhook and schedule events, but have no external source — `event_source_id` is NULL on the Event row. Subscribe a workflow to them via the Events UI or CLI using `bifrost events subscribe` with a NULL source and a matching `--event-type`.

## Subscribing to Internal Events

Internal-event subscriptions have `event_source_id = NULL` and match solely on `event_type`. To subscribe a workflow:

```bash
# CLI (once UI supports internal-event subscriptions directly)
bifrost events subscribe --event-type user.invited --workflow my-invite-workflow
```

The subscribed workflow receives the event payload under `context.event.data` (or `_event.body` as a workflow parameter).

---

## user.invited

**Event type:** `user.invited`
**Source type:** `internal`
**Source:** Bifrost platform — users router

### When emitted

- A platform admin calls `POST /api/users` with `invite: true` (and `trigger_automation` is `true` or omitted).
- A platform admin calls `POST /api/users/{id}/invite/resend`.

**Not emitted on:**
- `POST /api/users/{id}/invite/regenerate` — that path returns the link to the admin for manual delivery; no automation is triggered.
- `POST /api/users` with `invite: true, trigger_automation: false` — invite record is created so the link works, but no event fires.

### Payload

```jsonc
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",   // UUID of the new user
  "email": "alice@example.com",                          // New user's email
  "name": "Alice",                                       // Display name (empty string if not set)
  "registration_url": "https://app/accept-invite?token=...",  // Single-use registration link
  "expires_at": "2026-05-26T23:14:08+00:00",            // ISO-8601; link expires at this time
  "invited_by": {
    "user_id": "...",    // UUID of the admin who triggered the invite
    "email": "admin@example.com",
    "name": "Admin Name"
  },
  "reason": "created" | "resent"  // "created" on initial invite, "resent" on resend
}
```

### Scope

Scoped to the invited user's organization (`organization_id`). Global/platform-admin users have `organization_id = null`.

### Subscriber contract

The subscribed workflow receives the payload above under `_event.body` (or via `input_mapping` template variables). Recommended uses:

- Send a welcome/invite email via an email integration.
- Post a Slack notification to an onboarding channel.
- Log the invite to an audit system.
- Create a CRM contact.

### Example: subscribing a workflow

```bash
bifrost events subscribe --event-type user.invited --workflow send-invite-email
```

The `send-invite-email` workflow would then access:

```python
url = sdk.context.event["_event"]["body"]["registration_url"]
email = sdk.context.event["_event"]["body"]["email"]
```
