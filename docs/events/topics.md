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
