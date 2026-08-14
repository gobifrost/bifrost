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
import { createReconnectingSubscription } from "./ws-client";

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
  const channel = `execution:${executionId}`;
  return createReconnectingSubscription({
    channel,
    subscribeFrame: {
      type: "subscribe",
      channels: [{ name: channel }],
    },
    label: "execution stream",
    onSocketDown,
    onMessage: (msg) => {
      if (msg.type === "subscribed" && msg.channel === channel) {
        // The server adds the socket to the channel before acknowledging it.
        // This fires after every successful reconnect as well as the initial
        // connection, closing any gap with an authoritative REST read.
        onEvent({ type: "ready" });
      } else if (
        (msg.type === "error" &&
          (msg.channel === undefined || msg.channel === channel)) ||
        (msg.type === "subscription_revoked" && msg.channel === channel)
      ) {
        console.warn("[bifrost-sdk] execution stream subscription unavailable");
        onSocketDown?.();
        return false;
      } else if (
        msg.type === "execution_update" &&
        msg.executionId === executionId
      ) {
        const status = typeof msg.status === "string" ? msg.status : undefined;
        onEvent({
          type: "status",
          status,
          isTerminal: status ? TERMINAL_STATUSES.has(status) : false,
        });
      } else if (
        msg.type === "execution_log" &&
        msg.executionId === executionId
      ) {
        onEvent({
          type: "log",
          log: {
            level: String(msg.level ?? "info"),
            message: String(msg.message ?? ""),
            timestamp: String(msg.timestamp ?? ""),
            sequence:
              typeof msg.sequence === "number" ? msg.sequence : undefined,
          },
        });
      }
      return true;
    },
  });
}
