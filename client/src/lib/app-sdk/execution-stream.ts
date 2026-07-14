/**
 * Live subscription to one execution's status + log stream over the SDK
 * websocket. Mirrors `subscribeToTable` in ws-client.ts: one socket per
 * subscription, auth via buildWsUrl() (token query param under a provider
 * transport, cookies same-origin).
 *
 * Terminal `execution_update` frames carry status + duration_ms but NOT the
 * result (large results are not rebroadcast; see #483) — the caller fetches
 * `/api/executions/{id}` when isTerminal fires.
 */
import { buildWsUrl } from "./ws-client";

const TERMINAL_STATUSES = new Set([
  "Success",
  "Failed",
  "CompletedWithErrors",
  "Timeout",
  "Cancelled",
]);

export interface ExecutionStreamEvent {
  type: "status" | "log";
  status?: string;
  isTerminal?: boolean;
  log?: { level: string; message: string; timestamp: string; sequence?: number };
}

export function subscribeToExecution(
  executionId: string,
  onEvent: (evt: ExecutionStreamEvent) => void,
  onSocketDown?: () => void,
): () => void {
  const ws = new WebSocket(buildWsUrl());
  let closedByClient = false;
  let downFired = false;

  ws.addEventListener("open", () => {
    ws.send(
      JSON.stringify({
        type: "subscribe",
        channels: [{ name: `execution:${executionId}` }],
      }),
    );
  });

  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "execution_update" && msg.executionId === executionId) {
        onEvent({
          type: "status",
          status: msg.status,
          isTerminal: TERMINAL_STATUSES.has(msg.status),
        });
      } else if (msg.type === "execution_log" && msg.executionId === executionId) {
        onEvent({
          type: "log",
          log: {
            level: msg.level,
            message: msg.message,
            timestamp: msg.timestamp,
            sequence: msg.sequence,
          },
        });
      }
    } catch {
      // ignore unparseable frames — same policy as subscribeToTable
    }
  });

  ws.addEventListener("error", () => {
    console.warn("[bifrost-sdk] execution stream socket error");
    if (!closedByClient && !downFired) {
      downFired = true;
      onSocketDown?.();
    }
  });

  ws.addEventListener("close", (e) => {
    if (!closedByClient && !downFired) {
      downFired = true;
      console.warn("[bifrost-sdk] execution stream closed", e.code);
      onSocketDown?.();
    }
  });

  return () => {
    closedByClient = true;
    ws.close();
  };
}
