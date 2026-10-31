import type { components } from "@/lib/v1";
import {
  getBifrostTransport,
  getPlatformAuth,
  isRefreshRequiredChallenge,
  REQUEST_REPLAY_BUDGET,
} from "./transport";
import { subscribeToTable } from "./ws-client";
import {
  TableAccessDeniedError,
  TableNotFoundError,
} from "./errors";

export { TableAccessDeniedError, TableNotFoundError } from "./errors";

// Transport state lives in ./transport (shared with the ws client); re-export
// the setters/getters here so existing import sites keep working.
export { getBifrostTransport, setBifrostTransport } from "./transport";

type DocumentPublic = components["schemas"]["DocumentPublic"];
type DocumentQuery = components["schemas"]["DocumentQuery"];
type DocumentListResponse = components["schemas"]["DocumentListResponse"];
type DocumentCountResponse = components["schemas"]["DocumentCountResponse"];
type Expr = components["schemas"]["Expr"];

const base = "/api/tables";

function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * Append `?scope=<encoded>` (or `&scope=<encoded>` if a query string already
 * exists) to a path when scope is provided. Mirrors the Python SDK's
 * `scope: str | None` parameter — provider admins can target a specific org;
 * other callers omit it and the server defaults to the caller's org.
 */
/**
 * Default scope to apply when a caller doesn't pass one. Set by the app
 * shell on mount when the running app is org-scoped (`organization_id` is
 * not null in the bundle manifest), so `tables.*` and `useTable` calls
 * inside the app target the app's org rather than the user's home org.
 *
 * Mirrors the behavior of org-scoped *workflows*, which always run as their
 * org regardless of who triggered them. Global apps leave this null and
 * fall back to caller's-org behavior on the server.
 */
let defaultScope: string | null = null;

/**
 * Set the default scope for `tables.*` / `useTable` calls. Called by the
 * app shell on bundle mount; the cleanup return restores the previous value.
 */
export function setDefaultAppScope(scope: string | null): () => void {
  const prev = defaultScope;
  defaultScope = scope;
  return () => {
    defaultScope = prev;
  };
}

function withScope(path: string, scope?: string): string {
  // Caller-provided scope wins over the app default. Empty string is treated
  // as "no scope" (matches Python SDK's `scope: str | None = None` semantics).
  const effective = scope || defaultScope;
  if (!effective) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}scope=${encodeURIComponent(effective)}`;
}

/**
 * `null` sentinel = 404 result. Callers that want to distinguish "row missing"
 * from "table missing" should pass `throwOnNotFound: true` (used by ops where
 * the URL is table-level and 404 unambiguously means the table doesn't exist).
 */
async function http<T>(
  path: string,
  init: RequestInit = {},
  options: { throwOnNotFound?: boolean } = {},
): Promise<T | null> {
  const method = (init.method ?? "GET").toUpperCase();
  const transport = getBifrostTransport();
  const usingProvider = Boolean(transport.baseUrl || transport.headers);
  const url = transport.baseUrl
    ? `${transport.baseUrl.replace(/\/$/, "")}${path}`
    : path;
  const doFetch = transport.fetchImpl ?? fetch;
  // Built per attempt: a session refresh rotates the csrf_token cookie, so a
  // retry must carry the new value, not the one read before the refresh.
  const send = () => {
    // Same-origin (v1) uses cookie + CSRF. A provider transport (v2) carries its
    // own auth headers (bearer) and targets a possibly cross-origin baseUrl, so
    // CSRF/cookies don't apply.
    const csrfHeaders: Record<string, string> =
      usingProvider || method === "GET" || method === "HEAD"
        ? {}
        : { "X-CSRF-Token": getCsrfToken() };
    return doFetch(url, {
      ...init,
      credentials: usingProvider ? "omit" : "include",
      headers: {
        "content-type": "application/json",
        ...csrfHeaders,
        ...(transport.headers ?? {}),
        ...(init.headers ?? {}),
      },
    });
  };
  // Capture the v1 bridge token before the request. A delayed 401 can arrive
  // after another request already renewed the cookie session; in that case the
  // changed token proves this request should retry once without renewing again.
  let attemptedToken = usingProvider
    ? null
    : (getPlatformAuth()?.getAccessToken() ?? null);
  let r = await send();
  // v1 apps run on the host's session cookie. A provider transport (v2)
  // handles the same refresh_required challenge in its authenticated fetch.
  // A generic 401 might have been produced after an endpoint effect, so leave
  // it to the caller without replaying it.
  let remaining = REQUEST_REPLAY_BUDGET;
  while (remaining > 0 && isRefreshRequiredChallenge(r) && !usingProvider) {
    remaining -= 1;
    const auth = getPlatformAuth();
    if (auth?.canRefreshAccessToken()) {
      const freshToken = auth.getAccessToken();
      if (freshToken && freshToken !== attemptedToken) {
        attemptedToken = freshToken;
        r = await send();
      } else if (await auth.refreshAccessToken()) {
        attemptedToken = auth.getAccessToken();
        r = await send();
      } else {
        break;
      }
    } else {
      break;
    }
  }
  if (isRefreshRequiredChallenge(r) && !usingProvider) {
    const auth = getPlatformAuth();
    if (auth?.canRefreshAccessToken()) auth.handleAuthenticationFailure();
  }
  if (r.status === 403) {
    const body = await r.text().catch(() => "");
    throw new TableAccessDeniedError(body || "Access denied");
  }
  if (r.status === 404) {
    if (options.throwOnNotFound) {
      const body = await r.text().catch(() => "");
      throw new TableNotFoundError(body || "Table not found");
    }
    return null;
  }
  if (r.status === 204) return true as unknown as T;
  if (!r.ok) throw new Error(`tables: ${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export type TableChangeEvent =
  | {
      type: "document_change";
      action: "insert" | "update";
      row: DocumentPublic;
      table_id: string;
    }
  | {
      type: "document_change";
      action: "delete";
      row_id: string;
      table_id: string;
    }
  | { type: "table_invalidated"; table_id: string }
  | { type: "subscription_revoked"; channel: string }
  | { type: "error"; channel?: string; message: string };

export const tables = {
  /**
   * Fetch a single row by id. Returns `null` if the row doesn't exist.
   * Throws `TableAccessDeniedError` on 403.
   *
   * Note: 404 is ambiguous at this URL (could be missing table OR missing
   * row), so it returns `null` rather than throwing `TableNotFoundError`.
   */
  async get(
    table: string,
    id: string,
    scope?: string,
  ): Promise<DocumentPublic | null> {
    return http<DocumentPublic>(
      withScope(
        `${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`,
        scope,
      ),
    );
  },

  async insert(
    table: string,
    data:
      | Record<string, unknown>
      | Array<{ data: Record<string, unknown>; id?: string }>,
    scope?: string,
  ): Promise<DocumentPublic | DocumentPublic[]> {
    if (Array.isArray(data)) {
      const r = await http<{ documents: DocumentPublic[] }>(
        withScope(
          `${base}/${encodeURIComponent(table)}/documents/batch`,
          scope,
        ),
        { method: "POST", body: JSON.stringify({ documents: data }) },
        { throwOnNotFound: true },
      );
      return r!.documents;
    }
    const r = await http<DocumentPublic>(
      withScope(`${base}/${encodeURIComponent(table)}/documents`, scope),
      { method: "POST", body: JSON.stringify({ data }) },
      { throwOnNotFound: true },
    );
    return r!;
  },

  async upsert(
    table: string,
    item:
      | { id: string; data: Record<string, unknown> }
      | Array<{ id: string; data: Record<string, unknown> }>,
    scope?: string,
  ): Promise<DocumentPublic | DocumentPublic[]> {
    if (Array.isArray(item)) {
      const r = await http<{ documents: DocumentPublic[] }>(
        withScope(
          `${base}/${encodeURIComponent(table)}/documents/batch`,
          scope,
        ),
        {
          method: "POST",
          body: JSON.stringify({ documents: item, upsert: true }),
        },
        { throwOnNotFound: true },
      );
      return r!.documents;
    }
    const r = await http<DocumentPublic>(
      withScope(`${base}/${encodeURIComponent(table)}/documents`, scope),
      { method: "POST", body: JSON.stringify({ ...item, upsert: true }) },
      { throwOnNotFound: true },
    );
    return r!;
  },

  /**
   * Update a row's data. Returns `null` if the row doesn't exist.
   * Throws `TableAccessDeniedError` on 403.
   *
   * Note: 404 is ambiguous at this URL (could be missing table OR missing
   * row), so it returns `null` rather than throwing `TableNotFoundError`.
   */
  async update(
    table: string,
    id: string,
    data: Record<string, unknown>,
    scope?: string,
  ): Promise<DocumentPublic | null> {
    return http<DocumentPublic>(
      withScope(
        `${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`,
        scope,
      ),
      { method: "PATCH", body: JSON.stringify({ data }) },
    );
  },

  /**
   * Delete one or more rows. The single-id form is idempotent: deleting a
   * non-existent row returns `false` rather than throwing. The batch form
   * throws `TableNotFoundError` if the table itself is missing.
   * Both throw `TableAccessDeniedError` on 403.
   */
  async delete(
    table: string,
    id: string | string[],
    scope?: string,
  ): Promise<boolean | { deleted: number }> {
    if (Array.isArray(id)) {
      const r = await http<{ deleted: number }>(
        withScope(
          `${base}/${encodeURIComponent(table)}/documents/batch-delete`,
          scope,
        ),
        { method: "POST", body: JSON.stringify({ ids: id }) },
        { throwOnNotFound: true },
      );
      return r!;
    }
    const r = await http(
      withScope(
        `${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`,
        scope,
      ),
      { method: "DELETE" },
    );
    return r === true || r !== null;
  },

  async query(
    table: string,
    q: Partial<DocumentQuery> = {},
    scope?: string,
  ): Promise<DocumentListResponse> {
    const r = await http<DocumentListResponse>(
      withScope(
        `${base}/${encodeURIComponent(table)}/documents/query`,
        scope,
      ),
      { method: "POST", body: JSON.stringify(q) },
      { throwOnNotFound: true },
    );
    return r!;
  },

  async count(table: string, scope?: string): Promise<number> {
    const r = await http<DocumentCountResponse>(
      withScope(
        `${base}/${encodeURIComponent(table)}/documents/count`,
        scope,
      ),
      {},
      { throwOnNotFound: true },
    );
    return r!.count;
  },

  subscribe(
    tableId: string,
    filter: Expr | null,
    onEvent: (evt: TableChangeEvent) => void,
    onReconnect?: () => void,
  ): () => void {
    return subscribeToTable(tableId, filter, (msg) => {
      onEvent(msg as TableChangeEvent);
    }, onReconnect);
  },
};
