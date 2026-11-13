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
  /**
   * 1-indexed page number. Default `1`. Bumping `page` reloads the
   * snapshot for that window; `total` / `totalPages` come back to drive
   * pagination UI.
   */
  page?: number;
  /**
   * Rows per page. Default `100`, server hard cap `1000`. Setting this
   * larger than 1000 triggers a 422 from the server — the cap is in the
   * `DocumentQuery.limit` contract.
   */
  pageSize?: number;
  /**
   * JSONB data field to sort by (server: `data->>order_by`, compared as
   * text, `id` ascending as tiebreaker). When omitted the server orders by
   * the `created_at` column. Column names (`created_at`, `updated_at`,
   * `id`, ...) are NOT supported here — the server looks them up in the
   * JSONB `data`, where they are normally absent, so the result is
   * effectively ordered by `id`.
   *
   * Live inserts (and updates that change this field) are placed at their
   * sorted position in `rows`; see `applyPagedEvent` for the exact rules.
   */
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
  /**
   * Total number of rows that match `where` across all pages — not just
   * the current page. Use this to drive pagination UI (e.g.
   * `Math.ceil(total / pageSize)` for total pages, or `rows.length < total`
   * to detect "more on the server").
   */
  total: number;
  /**
   * Convenience: `Math.ceil(total / pageSize)`. The hook computes this
   * once per snapshot so callers don't have to thread `pageSize` to the
   * render site.
   */
  totalPages: number;
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
 * Live-updating table data hook with page-based pagination.
 *
 * Loads a snapshot of `page` (1-indexed) at `pageSize` rows per page via
 * `tables.query` and subscribes to live changes via `tables.subscribe`,
 * applying insert/update/delete events to the **current page window** only.
 *
 * Rows are returned in the **flat** shape — JSONB fields (e.g. `status`,
 * `assignee`) are spread at the top level alongside column-mapped fields
 * (`id`, `created_by`, `created_at`, etc.). This matches the shape websocket
 * events deliver, so live updates merge cleanly with the snapshot.
 *
 * Pagination semantics:
 *   - `total` is the count of rows matching `where` across all pages, not
 *     the count returned in `rows`. Drives "Page X of Y" UI.
 *   - With `order_by` set, live `insert` events are placed at their sorted
 *     position (matching the server's `tables.query` ordering) and updates
 *     that change the sort field move the row to its new position. Without
 *     `order_by` the server orders by `created_at`, so a new row is the
 *     newest: appended when ascending (the default), placed first on page 1
 *     when `order_dir: "desc"`.
 *   - Live `insert` events that fall outside the current page are dropped
 *     to keep the visible window stable (`total` still increments):
 *     an insert sorting after the last row of a full page, or — on
 *     `page > 1` — before the first row (it belongs to an earlier page).
 *     Navigating via a `page` re-render reissues the snapshot and they
 *     appear where they belong.
 *
 * @param name - Table name (or id) to query and subscribe to
 * @param query - Optional `where` / `page` / `pageSize` / order / scope
 * @returns `{ rows, total, totalPages, loading, error }`
 */
export function useTable(
  name: string,
  query: UseTableQuery = {},
): UseTableResult {
  const [rows, setRows] = useState<TableRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const {
    where,
    page = 1,
    pageSize = 100,
    order_by,
    order_dir,
    scope,
  } = query;
  // Effect deps below intentionally use JSON.stringify(where) since `where`
  // is an object whose identity changes per render. This keeps the effect
  // stable when callers pass an inline literal each render.
  const whereKey = JSON.stringify(where ?? null);

  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;
    let refreshInFlight = false;
    let refreshDirty = false;
    // Compute the offset/limit for this page. Server validates limit ≤ 1000;
    // a caller passing pageSize > 1000 will get the 422 surfaced via `error`.
    const offset = Math.max(0, (page - 1) * pageSize);
    const limit = pageSize;

    async function loadSnapshot() {
      const snap = await tables.query(
        name,
        { where, limit, offset, order_by, order_dir },
        scope,
      );
      if (cancelled) return null;
      setRows(snap.documents.map(flattenDocument));
      setTotal(snap.total);
      setError(null);
      setLoading(false);
      return snap;
    }

    function refreshAuthoritativeSnapshot() {
      if (refreshInFlight) {
        refreshDirty = true;
        return;
      }

      refreshInFlight = true;
      void (async () => {
        try {
          do {
            refreshDirty = false;
            try {
              await loadSnapshot();
            } catch (e) {
              if (!cancelled) {
                setError(e instanceof Error ? e : new Error(String(e)));
              }
            }
          } while (refreshDirty && !cancelled);
        } finally {
          refreshInFlight = false;
        }
      })();
    }

    async function init() {
      try {
        // Compile the filter once, before issuing the snapshot — if the
        // filter uses a query-only operator (contains/starts_with/...), we
        // surface the limitation as `error` immediately rather than letting
        // the snapshot succeed and the subscribe silently fail.
        const subscribeFilter: Expr | null = where
          ? compileFilterToExpr(where)
          : null;

        const snap = await loadSnapshot();
        if (!snap) return;

        // Subscribe by the canonical table UUID resolved server-side in the
        // requested scope. This sidesteps the cross-org name ambiguity that
        // `_resolve_table_id` would otherwise hit when subscribing by name.
        // The subscribe handler keeps the visible window at `pageSize` rows:
        // out-of-window inserts are dropped, in-window updates/deletes flow
        // through normally. Total is bumped/decremented optimistically so
        // pagination UI reflects the change without a refetch.
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
            if (evt.type === "table_invalidated") {
              refreshAuthoritativeSnapshot();
              return;
            }
            applyPagedEvent(evt, pageSize, setRows, setTotal, {
              order_by,
              order_dir,
              page,
            });
          },
          () => {
            // Events can be missed while the transport is down. Once the
            // server acknowledges the replacement subscription, replace the
            // visible window with a fresh authoritative snapshot.
            refreshAuthoritativeSnapshot();
          },
        );
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setLoading(false);
      }
    }

    void init();
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
    // `where` and `whereKey` are equivalent for dep tracking — we list both so
    // the lint rule sees the closed-over `where` and we still get value-based
    // change detection via `whereKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, whereKey, page, pageSize, order_by, order_dir, scope]);

  // `totalPages` for callers driving "Page X of Y" UI. Empty tables report
  // 0 pages (not 1) so a "no rows yet" state is unambiguous.
  const totalPages = total === 0 ? 0 : Math.ceil(total / pageSize);

  return { rows, total, totalPages, loading, error };
}

/**
 * Apply a `TableChangeEvent` to an accumulated, unbounded list (used by
 * `useInfiniteTable`). The loaded rows are the server's ordered prefix —
 * pages `0..n` concatenated — so the next `loadMore()` offset is
 * `rows.length`. Events keep that invariant:
 *
 *   - `insert`: placed at its sorted position (see `compareRows`). Without
 *     `order_by` the server orders by `created_at, id`, so a new row is the
 *     newest: last when ascending, first when descending. An insert that
 *     sorts after the last loaded row while `hasMore()` is true belongs to a
 *     page not yet loaded and is dropped — `loadMore()` will fetch it. An
 *     insert for an id already loaded is applied like an `update`.
 *   - `update`: replaced in place; with `order_by`, a changed sort key moves
 *     the row within the loaded rows (never dropped; see `replaceAt`).
 *   - `delete`: removed if loaded.
 *
 * A row whose update moves it past the loaded prefix, or a delete that lands
 * while a `loadMore()` fetch is in flight, can make the next page skip one
 * row until the next snapshot; `loadMore()` dedupes by id, so it
 * never shows a row twice.
 */
export function applyEvent(
  evt: TableChangeEvent,
  setRows: (updater: (prev: TableRow[]) => TableRow[]) => void,
  order: RowOrder & { hasMore?: () => boolean } = {},
) {
  const { order_by, order_dir, hasMore = () => false } = order;
  if (evt.type !== "document_change") return;
  if (evt.action === "insert") {
    // Websocket emits flat rows from server-side `_row_from_doc`; cast
    // through unknown because the OpenAPI-generated `row` type still
    // describes the nested-`data` shape.
    const inserted = evt.row as unknown as TableRow;
    setRows((prev) => {
      const dup = prev.findIndex((r) => r.id === inserted.id);
      if (dup !== -1) return replaceAt(prev, dup, inserted, order_by, order_dir);
      const idx = order_by
        ? sortedIndex(prev, inserted, order_by, order_dir)
        : order_dir === "desc"
          ? 0
          : prev.length;
      if (idx === prev.length && hasMore()) return prev;
      return [...prev.slice(0, idx), inserted, ...prev.slice(idx)];
    });
    return;
  }
  if (evt.action === "update") {
    const updated = evt.row as unknown as TableRow;
    setRows((prev) => {
      const at = prev.findIndex((r) => r.id === updated.id);
      return at === -1 ? prev : replaceAt(prev, at, updated, order_by, order_dir);
    });
    return;
  }
  if (evt.action === "delete") {
    const id = evt.row_id;
    setRows((prev) => prev.filter((r) => r.id !== id));
    return;
  }
}

export interface RowOrder {
  order_by?: string;
  order_dir?: "asc" | "desc";
}

/**
 * Fields that the flat row shape fills from Document columns. They shadow
 * any same-named key in the JSONB `data`, so the client cannot see the
 * value the server actually sorts on (`data->>field`).
 */
const COLUMN_FIELDS = new Set([
  "id",
  "table_id",
  "created_by",
  "updated_by",
  "created_at",
  "updated_at",
]);

// Approximates the database's default `en_US.UTF-8` text collation (case
// differences are secondary). It is not exact: ICU treats punctuation and
// spaces as significant, while glibc's en_US ignores them at the first level
// (e.g. text "-5" vs "10" order differently), and number text can differ
// from Postgres `->>` output for exponent/decimal forms (1e21, 1.0). A
// reconnect/invalidation snapshot replaces any drift with the server order.
const collator = new Intl.Collator("en-US");

/**
 * Client-side equivalent of the server's `data->>order_by` sort key:
 * strings as-is, numbers/booleans as their JSON text, objects/arrays as
 * JSON, missing/null as SQL NULL.
 */
function sortKey(row: TableRow, field: string): string | null {
  // The server sorts column names by the (normally absent) JSONB key, i.e.
  // NULL for every row — treat them as NULL so placement falls through to
  // the `id` tiebreaker exactly like the server does.
  if (COLUMN_FIELDS.has(field)) return null;
  const v = row[field];
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/**
 * Compare two rows the way `tables.query` orders them when `order_by` is
 * set: `ORDER BY data->>order_by [DESC], id`. Values compare as text (so
 * `10` sorts before `9`), NULLs sort last ascending / first descending
 * (Postgres default), and the `id` tiebreaker is always ascending.
 */
export function compareRows(
  a: TableRow,
  b: TableRow,
  order_by: string,
  order_dir: "asc" | "desc" = "asc",
): number {
  const ka = sortKey(a, order_by);
  const kb = sortKey(b, order_by);
  let c = 0;
  if (ka !== kb) {
    if (ka === null) c = 1;
    else if (kb === null) c = -1;
    else c = collator.compare(ka, kb);
  }
  if (order_dir === "desc") c = -c;
  if (c !== 0) return c;
  return collator.compare(String(a.id), String(b.id));
}

/** Index of the first row in `sorted` that `row` sorts strictly before. */
function sortedIndex(
  sorted: TableRow[],
  row: TableRow,
  order_by: string,
  order_dir?: "asc" | "desc",
): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (compareRows(row, sorted[mid]!, order_by, order_dir) < 0) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/**
 * Replace the row at `at` with `row`. With `order_by`, a changed sort key
 * moves it to its sorted position within the window (never dropping it).
 */
function replaceAt(
  rows: TableRow[],
  at: number,
  row: TableRow,
  order_by?: string,
  order_dir?: "asc" | "desc",
): TableRow[] {
  if (!order_by || sortKey(rows[at]!, order_by) === sortKey(row, order_by)) {
    return rows.map((r, i) => (i === at ? row : r));
  }
  const rest = [...rows.slice(0, at), ...rows.slice(at + 1)];
  const idx = sortedIndex(rest, row, order_by, order_dir);
  return [...rest.slice(0, idx), row, ...rest.slice(idx)];
}

/**
 * Apply a `TableChangeEvent` to the current page's rows, keeping the visible
 * window at `pageSize` rows and updating `total` so pagination UI stays in
 * sync. Used by `useTable` (paged); the accumulating variant `applyEvent` is
 * for `useInfiniteTable`.
 *
 * Window semantics:
 *   - `insert` matching the subscribe filter: increment `total` regardless.
 *     An insert for an id already in the window is applied like an
 *     `update` instead of duplicating the row (`total` is still bumped — rows and total are
 *     separate state, so a racing duplicate can leave `total` one high
 *     until the next snapshot).
 *     Without `order_by` the server orders by `created_at, id`, so a new
 *     row is newest: ascending, append iff there's room in the window (a
 *     full page keeps its rows; the insert is off-screen); descending,
 *     prepend on page 1 and drop on later pages. With `order_by`:
 *     insert at the row's sorted position, then trim to `pageSize` (the
 *     displaced trailing row belongs to the next page). The insert is
 *     dropped when it sorts after the last row of a full page, or, on
 *     `page > 1`, before the first row — it belongs to an earlier page, and
 *     the row that page would push onto this one is not known client-side.
 *   - `update`: replace the row in place. With `order_by`, if the sort key
 *     changed, the row is moved to its sorted position within the window. It is never dropped, even
 *     if its new key sorts past a window edge — it is pinned at that edge
 *     until the next snapshot. Updates for rows not in the window are
 *     ignored. The matching-filter check is the server's job — events only
 *     arrive if the filter still matches.
 *   - `delete`: remove from the window if present, decrement `total`.
 *
 * Client placement mirrors the server's text ordering (see `compareRows`);
 * a reconnect or `table_invalidated` snapshot replaces the window with the
 * authoritative order if they ever disagree.
 */
export function applyPagedEvent(
  evt: TableChangeEvent,
  pageSize: number,
  setRows: (updater: (prev: TableRow[]) => TableRow[]) => void,
  setTotal: (updater: (prev: number) => number) => void,
  order: RowOrder & { page?: number } = {},
) {
  const { order_by, order_dir, page = 1 } = order;
  if (evt.type !== "document_change") return;
  if (evt.action === "insert") {
    const inserted = evt.row as unknown as TableRow;
    setRows((prev) => {
      // Already in the window (e.g. an upsert's insert racing a refresh
      // snapshot that included it): apply it like an update, not a duplicate.
      const dup = prev.findIndex((r) => r.id === inserted.id);
      if (dup !== -1) return replaceAt(prev, dup, inserted, order_by, order_dir);
      if (!order_by) {
        // Server default order is `created_at, id` in `order_dir`. Ascending:
        // the newest row sorts last, so append iff there's room. Descending:
        // it sorts first — prepend on page 1; on later pages it belongs to
        // an earlier page.
        if (order_dir === "desc") {
          if (page > 1) return prev;
          return [inserted, ...prev].slice(0, pageSize);
        }
        const next = [...prev, inserted];
        return next.length > pageSize ? next.slice(0, pageSize) : next;
      }
      const idx = sortedIndex(prev, inserted, order_by, order_dir);
      // Sorts before this page's first row: belongs to an earlier page.
      if (page > 1 && idx === 0) return prev;
      // Sorts after the last row of a full page: belongs to a later page.
      if (idx === prev.length && prev.length >= pageSize) return prev;
      const next = [...prev.slice(0, idx), inserted, ...prev.slice(idx)];
      return next.length > pageSize ? next.slice(0, pageSize) : next;
    });
    setTotal((t) => t + 1);
    return;
  }
  if (evt.action === "update") {
    const updated = evt.row as unknown as TableRow;
    setRows((prev) => {
      const at = prev.findIndex((r) => r.id === updated.id);
      return at === -1 ? prev : replaceAt(prev, at, updated, order_by, order_dir);
    });
    return;
  }
  if (evt.action === "delete") {
    const id = evt.row_id;
    setRows((prev) => prev.filter((r) => r.id !== id));
    setTotal((t) => Math.max(0, t - 1));
    return;
  }
}
