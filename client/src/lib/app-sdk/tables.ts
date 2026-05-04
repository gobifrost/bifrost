import type { components } from "@/lib/v1";
import { subscribeToTable } from "./ws-client";

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
 * Thrown when the server returns 403 — the policy denied the operation.
 * Distinct from `TableNotFoundError`: the table exists in this scope, the
 * caller just isn't allowed to perform this action on it.
 */
export class TableAccessDeniedError extends Error {
  constructor(message = "Access denied") {
    super(message);
    this.name = "TableAccessDeniedError";
  }
}

/**
 * Thrown when the server returns 404 for a table-level operation — the table
 * does not exist in the requested scope. Distinct from `TableAccessDeniedError`:
 * the issue is that there's nothing to operate on, not that the caller is
 * forbidden from operating on it.
 *
 * For row-level reads/updates (`get`, `update`), 404 returns `null` instead of
 * throwing — the row may legitimately not exist and that's not an error.
 */
export class TableNotFoundError extends Error {
  constructor(message = "Table not found") {
    super(message);
    this.name = "TableNotFoundError";
  }
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
  const csrfHeaders: Record<string, string> =
    method === "GET" || method === "HEAD"
      ? {}
      : { "X-CSRF-Token": getCsrfToken() };
  const r = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...csrfHeaders,
      ...(init.headers ?? {}),
    },
  });
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
  ): () => void {
    return subscribeToTable(tableId, filter, (msg) => {
      onEvent(msg as TableChangeEvent);
    });
  },
};
