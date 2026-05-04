import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

// Capture the latest subscribe callback so tests can drive events.
const subscribeMock = vi.fn();
let lastOnEvent:
  | ((evt: Record<string, unknown>) => void)
  | null = null;

vi.mock("./ws-client", () => ({
  subscribeToTable: (
    _tableId: string,
    _filter: unknown,
    cb: (evt: Record<string, unknown>) => void,
  ) => {
    lastOnEvent = cb;
    subscribeMock(_tableId, _filter, cb);
    return () => {
      lastOnEvent = null;
    };
  },
}));

import { useTable } from "./use-table";

describe("useTable", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    subscribeMock.mockClear();
    lastOnEvent = null;
  });

  it("returns initial snapshot flattened to match the ws event shape", async () => {
    // API snapshot rows are nested ({id, data: {...}}); the hook flattens
    // them so consumers see a single shape across snapshot + live updates.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [{ id: "r1", data: { x: 1 } }],
          table_id: "tbl-uuid",
          total: 1,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0]?.id).toBe("r1");
    // JSONB fields are spread to the top level — `x` is reachable directly,
    // not via `.data.x`.
    expect((result.current.rows[0] as { x?: unknown }).x).toBe(1);
    expect(result.current.error).toBeNull();
  });

  it("applies inserts from subscribe (flat row shape)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(lastOnEvent).not.toBeNull());

    // Server emits flat rows from `_row_from_doc` — JSONB fields spread at
    // the top level alongside id/created_by/etc.
    act(() => {
      lastOnEvent?.({
        type: "document_change",
        action: "insert",
        row: { id: "r1", x: 1, table_id: "t1" },
        table_id: "t1",
      });
    });

    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0]?.id).toBe("r1");
    expect((result.current.rows[0] as { x?: unknown }).x).toBe(1);
  });

  it("applies updates by replacing the row with matching id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [
            { id: "r1", data: { x: 1 } },
            { id: "r2", data: { x: 2 } },
          ],
          table_id: "tbl-uuid",
          total: 2,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(lastOnEvent).not.toBeNull());

    act(() => {
      lastOnEvent?.({
        type: "document_change",
        action: "update",
        row: { id: "r1", x: 99, table_id: "tbl-uuid" },
        table_id: "tbl-uuid",
      });
    });

    expect(result.current.rows).toHaveLength(2);
    // After flattening, `x` is at top level on snapshot rows too — so
    // updates merge cleanly across snapshot and ws-event shapes.
    const r1 = result.current.rows.find((r) => r.id === "r1");
    expect((r1 as { x?: number } | undefined)?.x).toBe(99);
    const r2 = result.current.rows.find((r) => r.id === "r2");
    expect((r2 as { x?: number } | undefined)?.x).toBe(2);
  });

  it("applies deletes by row_id (covers visibility-loss)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [
            { id: "r1", data: { x: 1 } },
            { id: "r2", data: { x: 2 } },
          ],
          table_id: "tbl-uuid",
          total: 2,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(lastOnEvent).not.toBeNull());

    act(() => {
      lastOnEvent?.({
        type: "document_change",
        action: "delete",
        row_id: "r1",
        table_id: "tbl-uuid",
      });
    });

    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0]?.id).toBe("r2");
  });

  it("ignores non-document_change events (e.g. subscription_revoked)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [{ id: "r1", data: {} }],
          table_id: "tbl-uuid",
          total: 1,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(lastOnEvent).not.toBeNull());

    act(() => {
      lastOnEvent?.({
        type: "subscription_revoked",
        channel: "table:t1",
      });
    });

    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0]?.id).toBe("r1");
  });

  it("compiles the dict-shorthand `where` to a policy Expr for subscribe", async () => {
    // Snapshot uses the dict shorthand directly (server's _build_document_filters
    // expects field-keyed shape). Subscribe needs the operator-keyed Expr AST.
    // The hook compiles between the two so callers see one DSL.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTable("t1", { where: { status: "active" } }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(subscribeMock).toHaveBeenCalled());

    const [tableId, filterArg] = subscribeMock.mock.calls[0];
    // Subscribe goes by the canonical table UUID from the snapshot, NOT
    // the name passed to useTable. This sidesteps cross-org name ambiguity
    // when scope targets a different org.
    expect(tableId).toBe("tbl-uuid");
    // The compiled Expr is operator-keyed: { eq: [{row: "status"}, "active"] }
    expect(filterArg).toEqual({ eq: [{ row: "status" }, "active"] });

    // The snapshot POST body should carry the *original* dict-shorthand `where`.
    const reqBody = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(reqBody.where).toEqual({ status: "active" });
  });

  it("plumbs scope through to tables.query (REST snapshot is scope-aware)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTable("t1", { scope: "org-a" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    // The query URL should carry ?scope=org-a — proves scope reaches the
    // server and isn't silently dropped.
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toMatch(/\/documents\/query\?scope=org-a$/);
  });

  it("subscribes by the snapshot's table_id, not the table name", async () => {
    // Critical for cross-org subscriptions: when scope targets org A but
    // the caller's session belongs to org B, the same name may resolve to
    // different table UUIDs. The snapshot's `table_id` is the canonical
    // UUID for the target scope.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid-in-target-scope",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTable("tickets", { scope: "org-a" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(subscribeMock).toHaveBeenCalled());

    const [tableId] = subscribeMock.mock.calls[0];
    expect(tableId).toBe("tbl-uuid-in-target-scope");
    expect(tableId).not.toBe("tickets");
  });

  it("plumbs order_by/order_dir through to tables.query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTable("t1", { order_by: "created_at", order_dir: "desc" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    const reqBody = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(reqBody.order_by).toBe("created_at");
    expect(reqBody.order_dir).toBe("desc");
  });

  it("surfaces subscribe error frames as `error` (no longer silent)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(lastOnEvent).not.toBeNull());

    // Server sends `{type: "error", channel, message}` when a subscribe is
    // rejected. Before this fix, the hook ignored these and the caller saw
    // "snapshot loaded fine" with no live updates ever arriving.
    act(() => {
      lastOnEvent?.({
        type: "error",
        channel: "table:tbl-uuid",
        message: "Access denied",
      });
    });

    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.message).toBe("Access denied");
  });

  it("rejects query-only operators in `where` (contains/starts_with/...)", async () => {
    // The dict-shorthand DSL has more operators than the policy Expr AST.
    // For ones the AST can't represent, surface a clear error rather than
    // silently dropping the filter on the subscribe side.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [],
          table_id: "tbl-uuid",
          total: 0,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTable("t1", { where: { name: { contains: "acme" } } }),
    );
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.message).toContain("contains");
  });

  it("re-runs the effect when scope changes", async () => {
    // If the effect didn't depend on scope, switching providers' "selected
    // org" would silently keep the old subscription.
    // mockImplementation returns a fresh Response per call — Response
    // bodies can only be consumed once.
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            documents: [],
            table_id: "tbl-uuid",
            total: 0,
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ scope }: { scope: string }) => useTable("t1", { scope }),
      { initialProps: { scope: "org-a" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1));

    rerender({ scope: "org-b" });
    await waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(2));

    // Both queries fired: one per scope value. Verifies the effect
    // re-ran end-to-end (not just refetched and dropped the subscription).
    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes("scope=org-a"))).toBe(true);
    expect(urls.some((u) => u.includes("scope=org-b"))).toBe(true);
  });
});
