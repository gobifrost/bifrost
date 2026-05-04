import type { components } from "@/lib/v1";

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

export function subscribeToTable(
  tableId: string,
  filter: Expr | null,
  onEvent: (evt: TableChangeMessage) => void,
): () => void {
  const url = new URL("/ws/connect", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(url);
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
  return () => ws.close();
}
