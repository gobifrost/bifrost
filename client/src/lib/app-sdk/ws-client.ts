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

export function subscribeToTable(
  tableId: string,
  filter: Expr | null,
  onEvent: (evt: TableChangeMessage) => void,
): () => void {
  const ws = new WebSocket(buildWsUrl());
  let closedByClient = false;
  ws.addEventListener("open", () => {
    const channel: { name: string; filter?: Expr } = {
      name: `table:${tableId}`,
    };
    if (filter !== null) channel.filter = filter;
    ws.send(JSON.stringify({ type: "subscribe", channels: [channel] }));
  });
  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      onEvent(msg);
    } catch {
      // ignore unparseable messages
    }
  });
  // Without these, a wrong-origin or unauthenticated (4001) socket dies
  // silently: the snapshot loads but live updates just never arrive.
  ws.addEventListener("error", () => {
    console.warn("[bifrost-sdk] table subscription socket error");
  });
  ws.addEventListener("close", (e) => {
    if (!closedByClient) {
      console.warn("[bifrost-sdk] table subscription closed", e.code);
    }
  });
  return () => {
    closedByClient = true;
    ws.close();
  };
}
