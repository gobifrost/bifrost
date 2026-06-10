/**
 * Module-global transport state for the data SDK (`tables.*`, `useTable`,
 * and the live-subscribe websocket). Lives in its own module so both
 * `tables.ts` and `ws-client.ts` can read it without an import cycle
 * (tables → ws-client for `subscribeToTable`; ws-client → transport for
 * `getBifrostTransport`).
 *
 * Two modes:
 *
 * - **Default (v1 inline apps):** `baseUrl` empty + `fetchImpl` undefined →
 *   same-origin requests with cookie/CSRF auth (the platform serves the app,
 *   so the session cookie is present). Unchanged behavior.
 * - **v2 standalone apps:** `<BifrostProvider>` installs a transport pointing
 *   at the configured `baseUrl` with a bearer token (and optional org header),
 *   so `tables.*`/`useTable` reach the real Bifrost API even when the app is
 *   served by its own dev server (`npm run dev`) on a different origin.
 */
export interface BifrostTransport {
  baseUrl: string;
  /**
   * Raw bearer token. HTTP calls carry it via `headers.Authorization`; the
   * websocket client needs it separately because `WebSocket` cannot send
   * headers — the server accepts a `token` query param on `/ws/connect`.
   */
  token?: string;
  fetchImpl?: typeof fetch;
  headers?: Record<string, string>;
}

let transport: BifrostTransport = { baseUrl: "" };

/**
 * Install the transport the table SDK uses. Called by `<BifrostProvider>`
 * during render; the returned cleanup restores the prior transport on
 * unmount. v1 inline apps never call this and keep the same-origin cookie
 * default.
 */
export function setBifrostTransport(next: BifrostTransport): () => void {
  const prev = transport;
  transport = next;
  return () => {
    transport = prev;
  };
}

/** Read the currently installed transport (default: same-origin, no headers). */
export function getBifrostTransport(): BifrostTransport {
  return transport;
}
