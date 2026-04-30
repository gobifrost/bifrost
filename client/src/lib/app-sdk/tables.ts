import type { components } from "@/lib/v1";

type DocumentPublic = components["schemas"]["DocumentPublic"];
type DocumentQuery = components["schemas"]["DocumentQuery"];
type DocumentListResponse = components["schemas"]["DocumentListResponse"];
type DocumentCountResponse = components["schemas"]["DocumentCountResponse"];

const base = "/api/tables";

function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
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

export const tables = {
  async get(table: string, id: string): Promise<DocumentPublic | null> {
    return http<DocumentPublic>(
      `${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`,
    );
  },

  async insert(
    table: string,
    data: Record<string, unknown>,
    options?: { id?: string },
  ): Promise<DocumentPublic> {
    const body: Record<string, unknown> = { data };
    if (options?.id) body.id = options.id;
    const r = await http<DocumentPublic>(
      `${base}/${encodeURIComponent(table)}/documents`,
      { method: "POST", body: JSON.stringify(body) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async update(
    table: string,
    id: string,
    data: Record<string, unknown>,
  ): Promise<DocumentPublic | null> {
    return http<DocumentPublic>(
      `${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`,
      { method: "PATCH", body: JSON.stringify({ data }) },
    );
  },

  async upsert(
    table: string,
    id: string,
    data: Record<string, unknown>,
  ): Promise<DocumentPublic> {
    const r = await http<DocumentPublic>(
      `${base}/${encodeURIComponent(table)}/documents`,
      { method: "POST", body: JSON.stringify({ id, data, upsert: true }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async delete(table: string, id: string): Promise<boolean> {
    const r = await http(
      `${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
    return r === true || r !== null;
  },

  async query(
    table: string,
    q: Partial<DocumentQuery> = {},
  ): Promise<DocumentListResponse> {
    const r = await http<DocumentListResponse>(
      `${base}/${encodeURIComponent(table)}/documents/query`,
      { method: "POST", body: JSON.stringify(q) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async count(table: string): Promise<number> {
    const r = await http<DocumentCountResponse>(
      `${base}/${encodeURIComponent(table)}/documents/count`,
    );
    if (!r) return 0;
    return r.count;
  },

  async insert_batch(
    table: string,
    rows: Array<{ id?: string; data: Record<string, unknown> }>,
  ): Promise<{ inserted: number; errors: unknown[] }> {
    const r = await http<{ inserted: number; errors: unknown[] }>(
      `${base}/${encodeURIComponent(table)}/documents/batch`,
      { method: "POST", body: JSON.stringify({ documents: rows }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async upsert_batch(
    table: string,
    rows: Array<{ id: string; data: Record<string, unknown> }>,
  ): Promise<{ upserted: number; errors: unknown[] }> {
    const r = await http<{ upserted: number; errors: unknown[] }>(
      `${base}/${encodeURIComponent(table)}/documents/batch`,
      { method: "POST", body: JSON.stringify({ documents: rows, upsert: true }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async delete_batch(
    table: string,
    ids: string[],
  ): Promise<{ deleted: number }> {
    const r = await http<{ deleted: number }>(
      `${base}/${encodeURIComponent(table)}/documents/batch-delete`,
      { method: "POST", body: JSON.stringify({ ids }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  subscribe(
    table_id: string,
    onEvent: (evt: TableChangeEvent) => void,
  ): () => void {
    let cleanup: (() => void) | null = null;
    import("./ws-client").then(({ subscribeToTable }) => {
      cleanup = subscribeToTable(
        table_id,
        onEvent as Parameters<typeof subscribeToTable>[1],
      );
    });
    return () => cleanup?.();
  },
};

export type TableChangeEvent =
  | {
      type: "document_change";
      action: "insert" | "update" | "delete";
      id: string;
      data: Record<string, unknown> | null;
      created_by: string | null;
    }
  | { type: "subscription_revoked"; channel: string };
