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
function withScope(path: string, scope?: string): string {
  if (!scope) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}scope=${encodeURIComponent(scope)}`;
}

async function http<T>(
  path: string,
  init: RequestInit = {},
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
  if (r.status === 403) return null;
  if (r.status === 404) return null;
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
  | { type: "subscription_revoked"; channel: string };

export const tables = {
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
      );
      if (!r) throw new Error("Access denied");
      return r.documents;
    }
    const r = await http<DocumentPublic>(
      withScope(`${base}/${encodeURIComponent(table)}/documents`, scope),
      { method: "POST", body: JSON.stringify({ data }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
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
      );
      if (!r) throw new Error("Access denied");
      return r.documents;
    }
    const r = await http<DocumentPublic>(
      withScope(`${base}/${encodeURIComponent(table)}/documents`, scope),
      { method: "POST", body: JSON.stringify({ ...item, upsert: true }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

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
      );
      if (!r) throw new Error("Access denied");
      return r;
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
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async count(table: string, scope?: string): Promise<number> {
    const r = await http<DocumentCountResponse>(
      withScope(
        `${base}/${encodeURIComponent(table)}/documents/count`,
        scope,
      ),
    );
    if (!r) return 0;
    return r.count;
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
