import { useEffect, useState } from "react";
import type { components } from "@/lib/v1";
import { tables, type TableChangeEvent } from "./tables";

type DocumentPublic = components["schemas"]["DocumentPublic"];
type Expr = components["schemas"]["Expr"];

/**
 * A flat row as seen by `useTable` consumers. JSONB fields from `data` are
 * spread to the top level alongside column-mapped fields (`id`, `created_by`,
 * `created_at`, `updated_at`, `table_id`, `updated_by`). This matches the
 * shape websocket events deliver via the server's `_row_from_doc`, so the
 * initial snapshot and live updates share a single shape.
 *
 * Note: this intentionally diverges from the API contract type
 * `DocumentPublic` (which has a nested `data: {...}`). The flat shape is
 * authoritative on the realtime stream because the policy evaluator traverses
 * `{"row": "field"}` references against a flat dict; the snapshot is
 * normalized to match on receipt.
 */
export type TableRow = Record<string, unknown> & { id: string };

export interface UseTableQuery {
  where?: Expr;
  limit?: number;
  offset?: number;
  /**
   * Optional org scope. Provider admins can target a specific org; other
   * callers should omit it and the server defaults to the caller's org.
   * Mirrors the `scope: str | None` parameter on the Python SDK.
   */
  scope?: string;
}

export interface UseTableResult {
  rows: TableRow[];
  loading: boolean;
  error: Error | null;
}

/**
 * Flatten a `DocumentPublic` snapshot row into the flat shape that websocket
 * events emit (server-side `_row_from_doc`). JSONB fields go to the top level
 * alongside column-mapped fields.
 */
function flattenDocument(doc: DocumentPublic): TableRow {
  const data = (doc.data ?? {}) as Record<string, unknown>;
  return {
    ...data,
    id: doc.id,
    table_id: doc.table_id,
    created_by: doc.created_by,
    updated_by: doc.updated_by,
    created_at: doc.created_at,
    updated_at: doc.updated_at,
  };
}

/**
 * Live-updating table data hook.
 *
 * Loads an initial snapshot via `tables.query` and subscribes to live changes
 * via `tables.subscribe`, applying insert/update/delete events to local state.
 * The subscribe filter is the same `where` expression passed to the initial
 * query, so the websocket fanout sees exactly the same row visibility as the
 * snapshot.
 *
 * Rows are returned in the **flat** shape — JSONB fields (e.g. `status`,
 * `assignee`) are spread at the top level alongside column-mapped fields
 * (`id`, `created_by`, `created_at`, etc.). This matches the shape websocket
 * events deliver, so live updates merge cleanly with the snapshot.
 *
 * @param name - Table name (or id) to query and subscribe to
 * @param query - Optional `where`/`limit`/`offset` query parameters
 * @returns `{ rows, loading, error }`
 */
export function useTable(
  name: string,
  query: UseTableQuery = {},
): UseTableResult {
  const [rows, setRows] = useState<TableRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const { where, limit, offset, scope } = query;
  // Effect deps below intentionally use JSON.stringify(where) since `where`
  // is an object whose identity changes per render. This keeps the effect
  // stable when callers pass an inline literal each render.
  const whereKey = JSON.stringify(where ?? null);

  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;

    const filter = where ?? null;

    async function init() {
      try {
        const snap = await tables.query(name, { where, limit, offset }, scope);
        if (cancelled) return;
        setRows(snap.documents.map(flattenDocument));
        setLoading(false);

        // Subscribe by the canonical table UUID resolved server-side in the
        // requested scope. This sidesteps the cross-org name ambiguity that
        // `_resolve_table_id` would otherwise hit when subscribing by name.
        unsubscribe = tables.subscribe(snap.table_id, filter, (evt) => {
          applyEvent(evt, setRows);
        });
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      }
    }

    init();
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
    // `where` and `whereKey` are equivalent for dep tracking — we list both so
    // the lint rule sees the closed-over `where` and we still get value-based
    // change detection via `whereKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, whereKey, limit, offset, scope]);

  return { rows, loading, error };
}

function applyEvent(
  evt: TableChangeEvent,
  setRows: (updater: (prev: TableRow[]) => TableRow[]) => void,
) {
  if (evt.type !== "document_change") return;
  if (evt.action === "insert") {
    // Websocket emits flat rows from server-side `_row_from_doc`; cast
    // through unknown because the OpenAPI-generated `row` type still
    // describes the nested-`data` shape.
    const inserted = evt.row as unknown as TableRow;
    setRows((prev) => [...prev, inserted]);
    return;
  }
  if (evt.action === "update") {
    const updated = evt.row as unknown as TableRow;
    setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
    return;
  }
  if (evt.action === "delete") {
    const id = evt.row_id;
    setRows((prev) => prev.filter((r) => r.id !== id));
    return;
  }
}
