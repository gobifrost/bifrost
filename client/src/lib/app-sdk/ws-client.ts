export type TableChangeMessage = {
  type: "document_change" | "subscription_revoked" | "table_access_changed";
  table_id?: string;
  action?: string;
  id?: string;
  data?: Record<string, unknown> | null;
  created_by?: string | null;
  channel?: string;
};

export function subscribeToTable(
  tableId: string,
  onEvent: (evt: TableChangeMessage) => void,
): () => void {
  const url = new URL("/ws/connect", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("channels", `table:${tableId}`);
  const ws = new WebSocket(url);
  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data) as TableChangeMessage;
      onEvent(msg);
    } catch {
      // ignore unparseable messages
    }
  });
  return () => ws.close();
}
