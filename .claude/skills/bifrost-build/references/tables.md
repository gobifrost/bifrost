# Tables API — Python ↔ Web Side-by-Side Reference

**Python SDK** — workflow-side (server, async Python); use inside `.py` workflow functions.
**Web SDK** — app-side (browser, React/TypeScript); use inside TSX app components.

Both SDKs target the same `/api/tables` REST surface. They share method names but differ
in argument shapes, return types, error model, and — critically — what `delete` destroys.

---

## Side-by-Side Operation Table

| Operation | Python SDK (workflow-side) | Web SDK (app-side) | ★ Trap |
|-----------|---------------------------|-------------------|--------|
| **List tables** | `await sdk.tables.list(scope=None, app=None) -> list[TableInfo]` | — (admin REST only) | Web has no list call in the app SDK. |
| **Create table** | `await sdk.tables.create(name, description=None, table_schema=None, scope=None, app=None) -> TableInfo` | — | Create happens via CLI/API, not app SDK. |
| **★ Delete TABLE** | `await sdk.tables.delete(table_id: str) -> bool` | **does not exist** | ★ **delete divergence** — see below. |
| **Get row** | `await sdk.tables.get(table: str, doc_id: str, scope=None) -> DocumentData \| None` | `await tables.get(table, id, scope?) -> DocumentPublic \| null` | Python returns `None` on missing row. Web returns `null` but throws `TableAccessDeniedError` on 403, `TableNotFoundError` on 404 at table level. |
| **Insert (single)** | `await sdk.tables.insert(table, data: dict, id=None, scope=None, created_by=None) -> DocumentData` | `await tables.insert(table, data: Record<string,unknown>) -> DocumentPublic` | Python has a separate `insert_batch`; Web passes an array to the same `insert`. |
| **Insert (batch)** | `await sdk.tables.insert_batch(table, documents: list[dict], scope=None, created_by=None) -> BatchResult` | `await tables.insert(table, Array<{data, id?}>) -> DocumentPublic[]` | Same function name on Web, overloaded on argument type. |
| **Upsert (single)** | `await sdk.tables.upsert(table, id: str, data: dict, scope=None, created_by=None, updated_by=None) -> DocumentData` | `await tables.upsert(table, {id, data}) -> DocumentPublic` | ★ **arg shape differs** — Python takes `id` as a positional argument; Web wraps it in an object `{id, data}`. |
| **Upsert (batch)** | `await sdk.tables.upsert_batch(table, documents: list[dict], scope=None, created_by=None, updated_by=None) -> BatchResult` | `await tables.upsert(table, Array<{id, data}>) -> DocumentPublic[]` | Same overload pattern as insert batch on Web. |
| **Update row** | `await sdk.tables.update(table, doc_id: str, data: dict, scope=None, updated_by=None) -> DocumentData \| None` | `await tables.update(table, id: str, data: Record<string,unknown>, scope?) -> DocumentPublic \| null` | Merge-patch (PATCH) on both sides. Returns `None`/`null` if row not found. |
| **★ Delete ROW** | `await sdk.tables.delete_document(table, doc_id: str, scope=None) -> bool` | `await tables.delete(table, id: string) -> boolean` | ★ **different method names** — see delete trap below. |
| **Delete rows (batch)** | `await sdk.tables.delete_batch(table, doc_ids: list[str], scope=None) -> BatchDeleteResult` | `await tables.delete(table, ids: string[]) -> {deleted: number}` | Web auto-detects array; Python is a separate method. |
| **Query** | `await sdk.tables.query(table, where=None, order_by=None, order_dir='asc', limit=100, offset=0, scope=None) -> DocumentList` | `await tables.query(table, q: Partial<DocumentQuery>, scope?) -> DocumentListResponse` | ★ **call shape differs** — Python uses kwargs; Web takes a single options object. Result shape also differs (see below). |
| **Count** | `await sdk.tables.count(table, where=None, scope=None) -> int` | `await tables.count(table, scope?) -> number` | ★ **Web has no `where`** — filtered count is Python-only. Web always counts all rows. |
| **Live updates** | — | `tables.subscribe(tableId, filter, onEvent)` / `useTable(name, query)` / `useInfiniteTable` | Realtime is Web-only. |

---

## ★ The Delete Trap (Critical)

**Same name, different object destroyed:**

| | Python | Web |
|-|--------|-----|
| Delete the **table itself** | `sdk.tables.delete(table_id)` → `bool` | **no equivalent** |
| Delete a **row** | `sdk.tables.delete_document(table, doc_id)` → `bool` | `tables.delete(table, id)` → `boolean` |
| Delete **rows (batch)** | `sdk.tables.delete_batch(table, doc_ids)` → `BatchDeleteResult` | `tables.delete(table, ids[])` → `{deleted: number}` |

On Python: `tables.delete(...)` drops the **entire table** and all its data. Row deletion
is a different method (`delete_document` / `delete_batch`).

On Web: `tables.delete(...)` **always deletes rows**, never the table. Passing a string
deletes one row; passing an array deletes many.

---

## Insert / Upsert Arg Shape Differences

**Insert — single vs batch:**

```python
# Python: two separate methods
await sdk.tables.insert("tickets", {"status": "open"})
await sdk.tables.insert_batch("tickets", [{"status": "open"}, {"status": "closed"}])
```

```typescript
// Web: same method, overloaded on argument type
await tables.insert("tickets", { status: "open" });                      // single
await tables.insert("tickets", [{ data: { status: "open" } }, ...]);     // batch
```

Note the batch-item shape: Web wraps data under a `data` key (`{data, id?}`), unlike Python
which takes a plain `dict`.

**Upsert — positional id vs object:**

```python
# Python: id is a positional param
await sdk.tables.upsert("tickets", "ticket-123", {"status": "resolved"})
```

```typescript
// Web: id is inside an object
await tables.upsert("tickets", { id: "ticket-123", data: { status: "resolved" } });
```

---

## Query Differences

**Python — keyword arguments, nested result:**

```python
result = await sdk.tables.query(
    "tickets",
    where={"status": "open"},
    order_by="created_at",
    order_dir="desc",
    limit=50,
    offset=0,
)
# result: DocumentList
# rows live at result.documents[n].data  ← nested under .data
```

**Web — single options object, nested result (but `useTable` rows are FLAT):**

```typescript
const result = await tables.query("tickets", {
    where: { status: "open" },
    order_by: "created_at",
    order_dir: "desc",
    limit: 50,
    offset: 0,
});
// result: DocumentListResponse
// rows live at result.documents[n].data  ← still nested here
```

```typescript
// useTable hook: rows are FLATTENED — row.data is spread to top level
const { rows } = useTable("tickets", { where: { status: "open" } });
rows[0].status;      // ✓ works — data fields are at the top level
rows[0].data.status; // ✗ wrong — no nested .data on hook rows
rows[0].id;          // ✓ id, created_at, updated_at, etc. are also top-level
```

The flat shape comes from the server's `_row_from_doc` which spreads JSONB fields to the
top level; websocket events and the `useTable` snapshot both use this shape.

---

## Filter Operator Reference

The filter DSL is shared between Python (`where=`) and Web (`q.where`) and uses the same
server-side handler (`_build_document_filters`).

| Operator | Python | TypeScript | Notes |
|----------|--------|-----------|-------|
| Equality (shorthand) | `{"status": "open"}` | `{ status: "open" }` | String cast; JSONB containment for bool/number |
| `eq` | `{"n": {"eq": 5}}` | `{ n: { eq: 5 } }` | |
| `ne` | `{"n": {"ne": 5}}` | `{ n: { ne: 5 } }` or `{ n: { neq: 5 } }` | Server accepts `ne`; TS type also lists `neq` as alias |
| `gt` / `gte` / `lt` / `lte` | `{"amount": {"gte": 100}}` | `{ amount: { gte: 100 } }` | String cast comparison |
| `in` / `in_` | `{"status": {"in_": ["a","b"]}}` | `{ status: { in: ["a","b"] } }` | ★ Python uses `in_` to avoid keyword conflict; Web uses `in`; server accepts both |
| `is_null` | `{"deleted_at": {"is_null": True}}` | `{ deleted_at: { is_null: true } }` | |
| `has_key` | `{"metadata": {"has_key": True}}` | `{ metadata: { has_key: true } }` | JSONB key existence check |
| `contains` | `{"name": {"contains": "acme"}}` | `{ name: { contains: "acme" } }` | Case-insensitive substring (server uses `ILIKE` internally) |
| `starts_with` | `{"name": {"starts_with": "A"}}` | `{ name: { starts_with: "A" } }` | |
| `ends_with` | `{"name": {"ends_with": ".com"}}` | `{ name: { ends_with: ".com" } }` | |
| `ilike` | **IGNORED** | **IGNORED** | ★ `ilike` is silently dropped by the server — use `contains` instead |

**★ `ilike` is not a valid operator** — passing `{"name": {"ilike": "%acme%"}}` produces
no filter (silently matches everything). Use `contains` for case-insensitive substring
matching.

**★ `contains`/`starts_with`/`ends_with`/`has_key` cannot be used in `useTable.where`**
for live subscriptions. These operators have no equivalent in the policy `Expr` AST.
Using them throws an error surfaced in `useTable.error`; use `tables.query` directly for
one-shot reads.

---

## Error Model

| Situation | Python | Web |
|-----------|--------|-----|
| Row not found (get/update) | Returns `None` | Returns `null` |
| Table not found | Returns `None` or raises (varies by op) | Throws `TableNotFoundError` |
| Access denied (403) | Raises HTTP exception upstream | Throws `TableAccessDeniedError` |

```typescript
import { TableAccessDeniedError, TableNotFoundError } from "@/lib/app-sdk/tables";

try {
    const row = await tables.get("tickets", id);
    if (row === null) { /* row missing — not an error */ }
} catch (e) {
    if (e instanceof TableAccessDeniedError) { /* 403 */ }
    if (e instanceof TableNotFoundError)    { /* table doesn't exist */ }
}
```

---

## Live Updates (Web Only)

Python workflows have no realtime subscription mechanism. All three Web options are
backed by the same WebSocket channel.

**`tables.subscribe` (raw):**
```typescript
const unsub = tables.subscribe(
    tableId,           // must be UUID, not name
    filter,            // Expr | null — use compileFilterToExpr() from use-table.ts
    (evt) => { ... },  // TableChangeEvent handler
);
// cleanup:
unsub();
```

**`useTable` hook (paginated, live):**
```typescript
const { rows, total, totalPages, loading, error } = useTable("tickets", {
    where: { status: "open" },
    page: 1,
    pageSize: 25,
    order_by: "created_at",
    order_dir: "desc",
});
// rows are FLAT — row.status not row.data.status
```

`useTable` issues a snapshot via `tables.query` then subscribes for live changes.
Out-of-window inserts are dropped to keep the current page stable.

**`useInfiniteTable`** — unbounded accumulating list; rows are also flat. Use for
"load all / append-only" scenarios rather than paged UIs.

---

## Scope / Solution Cascade

**Python** — pass `scope=` as a string org ID to target a specific org. Omit for the
workflow's own org. Provider admins can pass any org; regular callers can only use their
own.

```python
# Run as the engine in org ABC, access org XYZ's table
result = await sdk.tables.query("shared-table", scope="org-xyz-uuid")
```

**Web** — the app shell calls `setDefaultAppScope(orgId)` on mount when the running app
is org-scoped. All `tables.*` and `useTable` calls in the app then target that org
without each call needing an explicit `scope`. Global apps leave the default null and
fall back to caller's-org behavior.

```typescript
// Explicit scope override — provider admins only
await tables.query("tickets", { limit: 50 }, "org-xyz-uuid");
```

**Solution workspaces** — tables deployed as part of a solution are solution-managed.
The solution's execution context carries `solution_id`; the resolver tries the solution's
own namespace first before falling back to org then global. Direct writes to
solution-managed table rows must go through the `sdk.tables.*` calls in the workflow, not
direct ORM; see `solutions.md` for the guard rules.
