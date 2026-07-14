/**
 * The v2 SDK's server-facing wire surface, as an explicit data structure so
 * `sdk-contract.test.ts` can snapshot-hash it. This is NOT executed against
 * the server — it's a manually-maintained inventory of every HTTP endpoint,
 * request-body key, and websocket frame field the SDK reads/writes, derived
 * by reading use-workflow.ts, execution-stream.ts, tables.ts, files.ts,
 * ws-client.ts, and transport.ts.
 *
 * When this object's hash changes (see sdk-contract.test.ts), the SDK's wire
 * surface changed. Refresh the snapshot if the change is non-breaking (e.g. a
 * new optional field); bump sdk-contract.json's version if it's breaking
 * (e.g. a field renamed/removed, or a response shape an old SDK depends on
 * changed incompatibly).
 */

export const wireSurface = {
  http: {
    // use-workflow.ts: useWorkflow().run()
    "POST /api/workflows/execute": {
      requestBody: ["workflow_id", "input_data", "app_id"],
      responseFields: [
        "execution_id",
        "status",
        "result",
        "error",
        "is_transient",
      ],
    },
    // use-workflow.ts: post-terminal-event result fetch + poll fallback
    "GET /api/executions/{id}": {
      responseFields: ["status", "result", "error_message"],
    },
    // tables.ts
    "GET /api/tables/{table}/documents/{id}": {},
    "POST /api/tables/{table}/documents": {
      requestBody: ["data", "upsert"],
    },
    "POST /api/tables/{table}/documents/batch": {
      requestBody: ["documents", "upsert"],
    },
    "PATCH /api/tables/{table}/documents/{id}": {
      requestBody: ["data"],
    },
    "DELETE /api/tables/{table}/documents/{id}": {},
    "POST /api/tables/{table}/documents/batch-delete": {
      requestBody: ["ids"],
    },
    "POST /api/tables/{table}/documents/query": {
      requestBody: [], // full DocumentQuery, passed through
    },
    "GET /api/tables/{table}/documents/count": {},
    // files.ts
    "POST /api/files/read": {
      requestBody: ["path", "location", "mode", "scope", "binary"],
    },
    "POST /api/files/write": {
      requestBody: ["path", "content", "location", "mode", "scope", "binary"],
    },
    "POST /api/files/delete": {
      requestBody: ["path", "location", "mode", "scope"],
    },
    "POST /api/files/list": {
      requestBody: [
        "directory",
        "location",
        "mode",
        "scope",
        "include_metadata",
      ],
    },
    "POST /api/files/exists": {
      requestBody: ["path", "location", "mode", "scope"],
    },
    "POST /api/files/signed-url": {
      requestBody: ["path", "method", "content_type", "location", "scope"],
    },
    "POST /api/files/signed-urls": {
      requestBody: ["requests"],
    },
    "POST /api/files/complete-upload": {
      requestBody: ["path", "content_type", "size_bytes", "location", "scope"],
    },
  },
  websocket: {
    // ws-client.ts / execution-stream.ts: /ws/connect, token via query param
    // when a provider transport is installed (cookie auth same-origin).
    connectPath: "/ws/connect",
    authQueryParam: "token",
    // Outbound subscribe frame (shared shape across table/file/execution
    // subscriptions).
    subscribeFrame: {
      type: "subscribe",
      channels: ["name", "filter", "scope"],
    },
    // Inbound frames read by tables.ts (via ws-client.ts subscribeToTable).
    tableChangeFrame: {
      type: "document_change",
      fields: ["table_id", "action", "row", "row_id", "channel"],
    },
    fileChangeFrame: {
      type: "file_change",
      fields: ["path", "action", "channel"],
    },
    // Inbound frames read by execution-stream.ts subscribeToExecution.
    // Terminal frames carry status but NOT the result (see #483) — the
    // caller does a follow-up GET /api/executions/{id}.
    executionUpdateFrame: {
      type: "execution_update",
      fields: ["executionId", "status"],
    },
    executionLogFrame: {
      type: "execution_log",
      fields: ["executionId", "level", "message", "timestamp", "sequence"],
    },
    subscriptionRevokedFrame: {
      type: "subscription_revoked",
      fields: ["channel"],
    },
    errorFrame: {
      type: "error",
      fields: ["channel", "message"],
    },
  },
} as const;
