# REST API Reference

The `bifrost api` command is an authenticated REST passthrough to the **Bifrost platform API only** — not third-party APIs. Use it for anything that does not have a dedicated CLI command, or for scripting raw API calls.

```bash
bifrost api GET /api/executions/{id}
bifrost api GET /api/executions?workflow_id={uuid}&status=Success
bifrost api POST /api/workflows/execute '{"workflow_id":"uuid","input_data":{},"sync":true}'
```

The command accepts an optional JSON body as the third positional argument, attaches the stored bearer token automatically, and returns the response body.

Use sparingly — dedicated commands (e.g. `bifrost workflows execute`, `bifrost forms create`) give better ergonomics and ref-resolution for UUIDs.

---

## Key Endpoints

For the complete endpoint list with request/response shapes, see `generated/openapi-digest.md`.

Common passthrough uses:

| Endpoint | Use |
|----------|-----|
| `GET /api/executions/{id}` | Fetch a specific execution record (logs, result, status) |
| `GET /api/executions` | List executions with filters (`workflow_id`, `status`, `limit`) |
| `POST /api/workflows/execute` | Execute a workflow directly (sync or async) |
| `GET /api/agent-runs/{id}` | Fetch an agent run record |
| `GET /api/apps/{slug}/validate` | Validate an app's source before publish |
| `GET /api/version` | Fetch the API version and contract version |

---

## App Validation

After writing or modifying app source files, validate before deploying:

```bash
bifrost api GET /api/apps/{slug}/validate
```

Returns a list of errors and warnings. Used by the platform before publish.

---

## Execution Polling

For one-off execution status checks (complement to the WebSocket stream `bifrost workflows execute` opens):

```bash
bifrost api GET /api/executions/{execution_id}
```

Response includes `status`, `result`, `error`, `logs`, `duration_ms`.

---

## What This Is NOT

- Not a passthrough to HaloPSA, Microsoft Graph, or any other external integration. Those are accessed from Python workflows via the `integrations` SDK module.
- Not a replacement for dedicated CLI commands. `bifrost api` is an escape hatch.
