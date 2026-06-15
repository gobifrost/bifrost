# Python SDK Reference

Inside a running Bifrost workflow, the SDK is available via top-level imports from the `bifrost` package. Each module is a singleton scoped to the current execution context (org, user, etc.).

For exact signatures, see `generated/python-sdk-signatures.md`. This file describes what each module is for and when to use it.

---

## tables

Read, write, and query JSON document rows in Bifrost tables.

```python
from bifrost import tables

# Insert a row
doc = await tables.insert("clients", {"name": "Acme", "status": "active"})

# Query with a filter
results = await tables.query("clients", where={"status": "active"}, limit=50)

# Get a single row by id
row = await tables.get("clients", doc_id=doc["id"])

# Update a row
await tables.update("clients", doc["id"], {"status": "churned"})

# Upsert (update-or-insert) by id
await tables.upsert("clients", id="known-uuid", data={"name": "Acme"})

# Delete a row
await tables.delete_document("clients", doc["id"])
```

The `scope` parameter on most calls targets a specific org (pass an org UUID); omit to use the workflow's own execution org. See `references/tables.md` for the full table data model, filter DSL, and policy rules.

---

## integrations

Read integration config and per-org mappings (OAuth credentials, API keys, etc.).

```python
from bifrost import integrations

# Get the integration config for the calling org's mapping
data = await integrations.get("halopsa")
# data.config: dict of config key→value
# data.oauth_token: OAuth token if set

# Get or create a per-entity mapping (e.g. sub-tenant)
mapping = await integrations.get_mapping("halopsa", entity_id="tenant-123")
await integrations.upsert_mapping("halopsa", scope=org_id, entity_id="tenant-123",
                                   entity_name="Acme", config={"api_key": "..."})
```

Use `integrations.get(name, scope=org_id)` to resolve config in a specific org's context. When `scope` is `None`, the cascade resolves the calling org's mapping.

---

## config

Read and write key-value configuration entries.

```python
from bifrost import config

# Get a config value (returns default if not set)
api_url = await config.get("halopsa_api_url", default="https://default.example.com")

# Set a value (optionally secret/encrypted)
await config.set("api_url", "https://api.example.com")
await config.set("api_key", "s3cr3t", is_secret=True)

# List all configs in scope
entries = await config.list()

# Delete a config
await config.delete("api_url")
```

The `scope` parameter on each call targets a specific org or None for global.

---

## files

Read and write files in the workspace object store (S3 / SeaweedFS).

```python
from bifrost import files

# Read a workspace file
content = await files.read("reports/template.html")

# Write a file
await files.write("reports/output.pdf", pdf_bytes_as_str)
await files.write_bytes("reports/output.pdf", pdf_bytes)

# Check existence
exists = await files.exists("reports/output.pdf")

# List files in a directory
listing = await files.list("reports/")

# Generate a pre-signed URL (for uploads or downloads)
url_info = await files.get_signed_url("uploads/file.csv", method="PUT")
```

The `location` parameter selects the storage area (`"workspace"` for the workspace repo, `"uploads"` for user uploads). `mode` is `"cloud"` (default) for the remote store.

---

## agents

Run a Bifrost AI agent from a workflow.

```python
from bifrost import agents

result = await agents.run("support-agent", input={"question": "How do I reset my password?"})
# result is a dict (structured) or str (text) depending on the agent's output
```

`timeout` defaults to 1800 seconds.

---

## forms

Read form metadata from a workflow.

```python
from bifrost import forms

form = await forms.get(form_id="uuid-here")
all_forms = await forms.list()
```

---

## workflows

Execute other registered workflows and query executions from within a workflow.

```python
from bifrost import workflows

# Execute another workflow (async — returns execution_id)
execution_id = await workflows.execute("notifications/send.py::send_email",
                                        input_data={"to": "user@example.com"})

# Schedule for later
execution_id = await workflows.execute("reports/daily.py::run",
                                        delay_seconds=3600)

# Get an execution record
execution = await workflows.get(execution_id)

# Cancel a scheduled execution
await workflows.cancel(execution_id)

# List executions
history = await workflows.list()
```

---

## executions

Inspect the current or other executions.

```python
from bifrost import executions

# Get the current execution's logs
logs = await executions.get_current_logs()

# Get a specific execution
ex = await executions.get(execution_id)

# List executions with filters
recent = await executions.list(workflow_name="onboard_user", status="Success", limit=10)
```

---

## knowledge

Semantic knowledge store — embed and retrieve text chunks.

```python
from bifrost import knowledge

# Store content
key = await knowledge.store("Acme Corp is a mid-market company in the logistics space.",
                             namespace="clients", key="acme-profile")

# Semantic search
results = await knowledge.search("logistics companies", namespace="clients", limit=5)
# results: list of KnowledgeDocument with .content, .key, .score, .metadata

# Delete a document
await knowledge.delete("acme-profile", namespace="clients")
```

---

## organizations

Manage organizations from a privileged workflow.

```python
from bifrost import organizations

orgs = await organizations.list()
org = await organizations.get(org_id)
new_org = await organizations.create(name="Acme", domain="acme.example.com")
await organizations.update(org_id, updates={"name": "Acme Corp"})
await organizations.delete(org_id)
```

---

## roles

Manage roles and their assignments.

```python
from bifrost import roles

role = await roles.create(name="Support Manager")
await roles.assign_users(role.id, user_ids=["user-uuid"])
await roles.assign_forms(role.id, form_ids=["form-uuid"])
```

---

## users

Manage users from a privileged workflow.

```python
from bifrost import users

user = await users.get(user_id)
all_users = await users.list(org_id=org_id)
new_user = await users.create(email="user@example.com", name="Jane Doe", org_id=org_id)
```

---

## ai

Call the configured LLM provider from a workflow.

```python
from bifrost import ai

# Text completion
response = await ai.complete(prompt="Summarize this text: ...", max_tokens=200)
# response.content: str

# Structured output (pass a Pydantic model class)
class Report(BaseModel):
    title: str
    summary: str

report = await ai.complete(prompt="Write a report about...", response_format=Report)
# report is a Report instance

# Streaming
async for chunk in ai.stream(prompt="Write a long document..."):
    print(chunk.content, end="")

# With knowledge retrieval
result = await ai.complete(prompt="What is our policy on X?",
                            knowledge=["policy-namespace"])
```

---

## events

Emit topic events that trigger subscribed workflows.

```python
from bifrost import events

result = await events.emit("acme.deal_won", {"amount": 50000, "deal_id": "d-123"})
# result: {"event_id": "...", "subscribers_notified": 2}
```

When a workflow is triggered by a topic event, `context.event` is populated:
- `context.event.type` — the topic string
- `context.event.data` — payload dict
- `context.event.organization_id` — org stamped at emit time
- `context.event.received_at` — ISO-8601 timestamp
