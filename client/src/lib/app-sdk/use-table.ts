import { useEffect, useState } from "react";
import type { components } from "@/lib/v1";
import { tables, type TableChangeEvent } from "./tables";

type DocumentPublic = components["schemas"]["DocumentPublic"];
type Expr = components["schemas"]["Expr"];

/**
 * Field-keyed filter DSL — same shape `tables.query` parses on the server
 * (`_build_document_filters` in `api/src/routers/tables.py`). Mirrors the
 * Python SDK's `where` parameter so app authors learn one DSL across both.
 *
 * Examples:
 *   `{ status: "active" }`              → equality
 *   `{ amount: { gte: 100, lt: 1000 } }`→ comparison
 *   `{ name: { contains: "acme" } }`    → substring (server-side `ILIKE`)
 *   `{ category: { in: ["a", "b"] } }`  → set membership
 *   `{ deleted_at: { is_null: true } }` → null check
 *
 * For the equality short-form, the value type matters: `{ active: true }`
 * uses JSONB containment (type-safe for booleans/numbers), while
 * `{ name: "Acme" }` casts to text and compares.
 */
export type FilterValue =
  | string
  | number
  | boolean
  | null
  | {
      eq?: unknown;
      neq?: unknown;
      ne?: unknown;
      contains?: string;
      starts_with?: string;
      ends_with?: string;
      gt?: unknown;
      gte?: unknown;
      lt?: unknown;
      lte?: unknown;
      in?: unknown[];
      is_null?: boolean;
      has_key?: boolean;
    };

export type DocumentFilter = Record<string, FilterValue>;

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
  /**
   * Filter conditions in the field-keyed dict shorthand DSL.
   *
   * The hook compiles this to the policy `Expr` AST internally for the
   * subscribe call, so the snapshot and the realtime fanout see the same
   * row visibility.
   *
   * Note: a few operators (`contains`, `starts_with`, `ends_with`,
   * `has_key`) are supported by the query path but cannot be expressed as
   * a policy `Expr` today. Using them in `useTable.where` will throw a
   * clear error via `error` — split into a query-only call (`tables.query`)
   * if you need them without live updates.
   */
  where?: DocumentFilter;
  limit?: number;
  offset?: number;
  /** Field name to sort by. Defaults to `updated_at` server-side. */
  order_by?: string;
  /** Sort direction. Defaults to `asc` server-side. */
  order_dir?: "asc" | "desc";
  /**
   * Optional org scope. Provider admins can target a specific org; other
   * callers should omit it and the server defaults to the caller's org.
   * Mirrors the `scope: str | None` parameter on the Python SDK.
   */
  scope?: string;
}

/**
 * Compile the field-keyed dict-shorthand filter into the operator-keyed
 * policy `Expr` AST that `tables.subscribe` validates server-side. Pure
 * function, no React, fully unit-testable.
 *
 * Returns `null` for an empty filter (no conjuncts) — the caller should
 * pass `null` to `tables.subscribe` to subscribe with no filter.
 *
 * Throws if the filter uses an operator the policy AST doesn't support
 * (`contains`/`starts_with`/`ends_with`/`has_key`). Surfaced to the caller
 * via the hook's `error` state so the limitation is visible, not silent.
 */
export function compileFilterToExpr(filter: DocumentFilter): Expr | null {
  const conjuncts: unknown[] = [];

  for (const [field, value] of Object.entries(filter)) {
    if (value === null) {
      // `{ field: null }` shorthand → is_null check. The Expr validator
      // rejects null literals in eq/neq (NULL semantics differ between the
      // evaluator and SQL pushdown), so we route through is_null here.
      conjuncts.push({ is_null: { row: field } });
      continue;
    }
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      // Shorthand equality: { status: "active" }
      conjuncts.push({ eq: [{ row: field }, value] });
      continue;
    }

    // Operator object: { amount: { gte: 100 } }
    for (const [op, opVal] of Object.entries(value)) {
      switch (op) {
        case "eq":
          if (opVal === null) {
            conjuncts.push({ is_null: { row: field } });
          } else {
            conjuncts.push({ eq: [{ row: field }, opVal] });
          }
          break;
        case "neq":
        case "ne":
          if (opVal === null) {
            conjuncts.push({ not: { is_null: { row: field } } });
          } else {
            conjuncts.push({ neq: [{ row: field }, opVal] });
          }
          break;
        case "lt":
        case "lte":
        case "gt":
        case "gte":
          conjuncts.push({ [op]: [{ row: field }, opVal] });
          break;
        case "in":
          if (!Array.isArray(opVal) || opVal.length === 0) {
            throw new Error(
              `useTable: \`in\` operator on field '${field}' requires a ` +
                `non-empty array`,
            );
          }
          conjuncts.push({ in: [{ row: field }, opVal] });
          break;
        case "is_null":
          conjuncts.push(
            opVal
              ? { is_null: { row: field } }
              : { not: { is_null: { row: field } } },
          );
          break;
        case "contains":
        case "starts_with":
        case "ends_with":
        case "has_key":
          throw new Error(
            `useTable: operator '${op}' is supported by tables.query but cannot ` +
              `be used as a live-subscribe filter (policy Expr AST has no ` +
              `equivalent). Drop it from \`where\` and filter client-side, or ` +
              `use \`tables.query\` directly for one-shot reads.`,
          );
        default:
          throw new Error(`useTable: unknown operator '${op}' on field '${field}'`);
      }
    }
  }

  // Empty filter → null. Server's Expr validator rejects `{and: []}` (logic
  // ops require ≥2 operands), so we return null and let callers subscribe
  // without a filter.
  if (conjuncts.length === 0) return null;
  if (conjuncts.length === 1) return conjuncts[0] as Expr;
  return { and: conjuncts } as unknown as Expr;
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
export function flattenDocument(doc: DocumentPublic): TableRow {
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

  const { where, limit, offset, order_by, order_dir, scope } = query;
  // Effect deps below intentionally use JSON.stringify(where) since `where`
  // is an object whose identity changes per render. This keeps the effect
  // stable when callers pass an inline literal each render.
  const whereKey = JSON.stringify(where ?? null);

  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;

    async function init() {
      try {
        // Compile the filter once, before issuing the snapshot — if the
        // filter uses a query-only operator (contains/starts_with/...), we
        // surface the limitation as `error` immediately rather than letting
        // the snapshot succeed and the subscribe silently fail.
        const subscribeFilter: Expr | null = where
          ? compileFilterToExpr(where)
          : null;

        const snap = await tables.query(
          name,
          { where, limit, offset, order_by, order_dir },
          scope,
        );
        if (cancelled) return;
        setRows(snap.documents.map(flattenDocument));
        setLoading(false);

        // Subscribe by the canonical table UUID resolved server-side in the
        // requested scope. This sidesteps the cross-org name ambiguity that
        // `_resolve_table_id` would otherwise hit when subscribing by name.
        unsubscribe = tables.subscribe(
          snap.table_id,
          subscribeFilter,
          (evt) => {
            if (evt.type === "error") {
              // Server rejected the subscribe (table not found / policy
              // missing / access denied). Without surfacing this, the
              // snapshot would appear "live" but never receive updates —
              // silently broken.
              if (!cancelled) setError(new Error(evt.message));
              return;
            }
            applyEvent(evt, setRows);
          },
        );
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
  }, [name, whereKey, limit, offset, order_by, order_dir, scope]);

  return { rows, loading, error };
}

export function applyEvent(
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
