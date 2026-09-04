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
 *   at the configured `baseUrl` with an authenticated fetch and a lazy token
 *   reader for WebSocket reconnects, so `tables.*`/`useTable` reach the real
 *   Bifrost API even when the app is served by its own dev server.
 */
export interface BifrostTransport {
  baseUrl: string;
  /**
	 * Initial bearer token. Updated providers prefer `getToken`; this remains as
	 * the local-development and older-provider fallback.
   */
  token?: string;
	/** Read the current token at connection time (deployed V2 apps rotate it). */
	getToken?: () => string | undefined;
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
