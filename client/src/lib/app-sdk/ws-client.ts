import type { components } from "@/lib/v1";
import { getBifrostTransport } from "./transport";

type Expr = components["schemas"]["Expr"];

export type TableChangeMessage = {
  type: "document_change" | "subscription_revoked" | "error";
  table_id?: string;
  action?: "insert" | "update" | "delete";
  row?: Record<string, unknown> | null;
  row_id?: string | null;
  channel?: string;
  // Populated on `type: "error"` frames — server sends these when a
  // subscribe is rejected (table not found / policy missing / access denied).
  // See `_authorize_table_subscribe` in api/src/routers/websocket.py.
  message?: string;
};

export type FileChangeMessage =
  | {
      type: "file_change";
      path?: string;
      action?: "write" | "delete" | "rename" | "upload";
      channel?: string;
    }
  | { type: "subscription_revoked"; channel: string }
  | { type: "error"; channel?: string; message: string };

/**
 * Build the `/ws/connect` URL through the installed transport. With a
 * provider transport (npm-dev / `solution start` — possibly cross-origin),
 * the socket must target the transport's baseUrl, and since `WebSocket`
 * cannot send an Authorization header, auth rides as a `token` query param
 * (accepted by the server's ws auth). The same-origin default (v1 inline
 * apps) keeps cookie auth and sends no token.
 */
export function buildWsUrl(): string {
  const t = getBifrostTransport();
  const base = t.baseUrl ? new URL(t.baseUrl) : new URL(window.location.href);
  const proto = base.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/ws/connect", `${proto}//${base.host}`);
  if (t.token) url.searchParams.set("token", t.token);
  return url.toString();
}

const NON_RETRYABLE_CLOSE_CODES = new Set([4001, 4003]);
const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 30_000;

type SubscriptionMessage = Record<string, unknown>;

interface ReconnectingSubscriptionOptions {
  channel: string;
  subscribeFrame: Record<string, unknown>;
  label: string;
  onMessage: (message: SubscriptionMessage) => boolean | void;
  onReconnect?: () => void;
  onSocketDown?: () => void;
}

/**
 * Keep one logical subscription alive across transient WebSocket closures.
 * A connection only counts as restored after the server acknowledges the
 * channel, so callers can safely refresh snapshots without racing the
 * subscribe handshake. Authentication/policy close codes are terminal.
 */
export function createReconnectingSubscription({
  channel,
  subscribeFrame,
  label,
  onMessage,
  onReconnect,
  onSocketDown,
}: ReconnectingSubscriptionOptions): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let reconnectAttempt = 0;
  let hasSubscribed = false;
  let socketDownFired = false;

  const notifySocketDown = () => {
    if (!stopped && !socketDownFired) {
      socketDownFired = true;
      onSocketDown?.();
    }
  };

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    socket?.close();
    socket = null;
  };

  const connect = () => {
    if (stopped) return;

    const current = new WebSocket(buildWsUrl());
    socket = current;

    current.addEventListener("open", () => {
      current.send(JSON.stringify(subscribeFrame));
    });

    current.addEventListener("message", (event) => {
      let message: SubscriptionMessage;
      try {
        message = JSON.parse(event.data) as SubscriptionMessage;
      } catch {
        return;
      }

      if (message.type === "subscribed" && message.channel === channel) {
        const reconnected = hasSubscribed || reconnectAttempt > 0;
        hasSubscribed = true;
        reconnectAttempt = 0;
        socketDownFired = false;
        if (reconnected) onReconnect?.();
      }

      if (onMessage(message) === false) stop();
    });

    current.addEventListener("error", () => {
      if (stopped) return;
      notifySocketDown();
    });

    current.addEventListener("close", (event) => {
      if (stopped || current !== socket) return;
      socket = null;
      notifySocketDown();

      if (NON_RETRYABLE_CLOSE_CODES.has(event.code)) {
        console.warn(`[bifrost-sdk] ${label} closed`, event.code);
        return;
      }

      const baseDelay = Math.min(
        RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempt,
        RECONNECT_MAX_DELAY_MS,
      );
      reconnectAttempt += 1;
      const delay = Math.min(
        Math.round(baseDelay * (0.75 + Math.random() * 0.5)),
        RECONNECT_MAX_DELAY_MS,
      );
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delay);
    });
  };

  connect();
  return stop;
}

export function subscribeToTable(
  tableId: string,
  filter: Expr | null,
  onEvent: (evt: TableChangeMessage) => void,
  onReconnect?: () => void,
): () => void {
  const channelName = `table:${tableId}`;
  const channel: { name: string; filter?: Expr } = { name: channelName };
  if (filter !== null) channel.filter = filter;

  return createReconnectingSubscription({
    channel: channelName,
    subscribeFrame: { type: "subscribe", channels: [channel] },
    label: "table subscription",
    onReconnect,
    onMessage: (message) => {
      onEvent(message as TableChangeMessage);
      return !(
        message.type === "error" || message.type === "subscription_revoked"
      );
    },
  });
}

export function subscribeToFiles(
  location: string,
  prefix: string,
  scope: string | null | undefined,
  onEvent: (evt: FileChangeMessage) => void,
  onReconnect?: () => void,
): () => void {
  const channel = `files:${location}:${prefix}`;
  return createReconnectingSubscription({
    channel,
    subscribeFrame: {
      type: "subscribe",
      channels: [{ name: channel, scope: scope ?? undefined }],
    },
    label: "file subscription",
    onReconnect,
    onMessage: (message) => {
      onEvent(message as FileChangeMessage);
      return !(
        message.type === "error" || message.type === "subscription_revoked"
      );
    },
  });
}
