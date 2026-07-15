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
  type: "ready" | "status" | "log";
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
  const channel = `execution:${executionId}`;
  let closedByClient = false;
  let downFired = false;

  const fireSocketDown = () => {
    if (!closedByClient && !downFired) {
      downFired = true;
      onSocketDown?.();
    }
  };

  ws.addEventListener("open", () => {
    ws.send(
      JSON.stringify({
        type: "subscribe",
        channels: [{ name: channel }],
      }),
    );
  });

  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "subscribed" && msg.channel === channel) {
        // The server adds the socket to the channel before acknowledging it.
        // A result check at this point closes the gap between the initial GET
        // and the live subscription becoming authoritative.
        onEvent({ type: "ready" });
      } else if (
        (msg.type === "error" && (msg.channel === undefined || msg.channel === channel)) ||
        (msg.type === "subscription_revoked" && msg.channel === channel)
      ) {
        console.warn("[bifrost-sdk] execution stream subscription unavailable");
        fireSocketDown();
        ws.close();
      } else if (msg.type === "execution_update" && msg.executionId === executionId) {
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
    fireSocketDown();
  });

  ws.addEventListener("close", (e) => {
    if (!closedByClient && !downFired) {
      console.warn("[bifrost-sdk] execution stream closed", e.code);
      fireSocketDown();
    }
  });

  return () => {
    closedByClient = true;
    ws.close();
  };
}
