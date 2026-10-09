import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

// Capture the latest subscribe callback so tests can drive events.
const subscribeMock = vi.fn();
let lastOnEvent:
  | ((evt: Record<string, unknown>) => void)
  | null = null;
let lastOnReconnect: (() => void) | null = null;

vi.mock("./ws-client", () => ({
  subscribeToTable: (
    _tableId: string,
    _filter: unknown,
    cb: (evt: Record<string, unknown>) => void,
    onReconnect?: () => void,
  ) => {
    lastOnEvent = cb;
    lastOnReconnect = onReconnect ?? null;
    subscribeMock(_tableId, _filter, cb);
    return () => {
      lastOnEvent = null;
      lastOnReconnect = null;
    };
  },
}));

import { useTable } from "./use-table";
import type { PlatformAuthBridge } from "./transport";

type AuthGlobal = typeof globalThis & { __BIFROST_PLATFORM_AUTH_V1__?: PlatformAuthBridge };

function makePage(ids: string[], total: number, table_id = "tbl-uuid") {
  return new Response(
    JSON.stringify({
      documents: ids.map((id) => ({ id, data: { label: id } })),
      table_id,
      total,
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function deferredPage(ids: string[], total: number, table_id = "tbl-uuid") {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((r) => {
    resolve = r;
  });
  return {
    promise,
    resolve: () => resolve(makePage(ids, total, table_id)),
  };
}

describe("useTable", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    subscribeMock.mockClear();
    lastOnEvent = null;
    lastOnReconnect = null;
  });

  afterEach(() => {
    delete (globalThis as AuthGlobal).__BIFROST_PLATFORM_AUTH_V1__;
  });

  it("recovers the snapshot after the session cookie lapses (v1 renewal)", async () => {
    const refreshAccessToken = vi.fn(async () => true);
    (globalThis as AuthGlobal).__BIFROST_PLATFORM_AUTH_V1__ = {
      getAccessToken: () => null,
      canRefreshAccessToken: () => true,
      refreshAccessToken,
      handleAuthenticationFailure: vi.fn(),
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response('{"detail":"Not authenticated"}', { status: 401 }))
        .mockResolvedValueOnce(makePage(["p1", "p2"], 2)),
    );

    const { result } = renderHook(() => useTable("crm-providers-local"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
    expect(result.current.rows.map((r) => r.id)).toEqual(["p1", "p2"]);
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
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

  it("refreshes the authoritative page snapshot after a reconnect", async () => {
    const response = (id: string, value: number) =>
      new Response(
        JSON.stringify({
          documents: [{ id, data: { value } }],
          table_id: "tbl-uuid",
          total: 1,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response("before", 1))
      .mockResolvedValueOnce(response("after", 2));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1"));
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("before"));

    act(() => lastOnReconnect?.());

    await waitFor(() => expect(result.current.rows[0]?.id).toBe("after"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refreshes the authoritative page snapshot after table invalidation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["before"], 1))
      .mockResolvedValueOnce(makePage(["after", "extra"], 5));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTable("t1", {
        where: { status: "active" },
        page: 3,
        pageSize: 2,
        order_by: "created_at",
        order_dir: "desc",
        scope: "org-a",
      }),
    );
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("before"));

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });

    await waitFor(() =>
      expect(result.current.rows.map((row) => row.id)).toEqual([
        "after",
        "extra",
      ]),
    );
    expect(result.current.total).toBe(5);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      where: { status: "active" },
      limit: 2,
      offset: 4,
      order_by: "created_at",
      order_dir: "desc",
    });
    expect(fetchMock.mock.calls[1][0]).toMatch(/scope=org-a$/);
  });

  it("coalesces an invalidation burst during one pass into one subsequent refresh", async () => {
    const firstRefresh = deferredPage(["first"], 1);
    const trailingRefresh = deferredPage(["trailing"], 1);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["initial"], 1))
      .mockReturnValueOnce(firstRefresh.promise)
      .mockReturnValueOnce(trailingRefresh.promise);
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("initial"));

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(result.current.rows[0]?.id).toBe("initial");

    await act(async () => {
      firstRefresh.resolve();
      await firstRefresh.promise;
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(result.current.rows[0]?.id).toBe("first");

    await act(async () => {
      trailingRefresh.resolve();
      await trailingRefresh.promise;
    });

    await waitFor(() => expect(result.current.rows[0]?.id).toBe("trailing"));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("queues one additional refresh for invalidations during the trailing refresh", async () => {
    const firstRefresh = deferredPage(["first"], 1);
    const trailingRefresh = deferredPage(["trailing"], 1);
    const finalRefresh = deferredPage(["final"], 1);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["initial"], 1))
      .mockReturnValueOnce(firstRefresh.promise)
      .mockReturnValueOnce(trailingRefresh.promise)
      .mockReturnValueOnce(finalRefresh.promise)
      .mockResolvedValueOnce(makePage(["later"], 1));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("initial"));

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    await act(async () => {
      firstRefresh.resolve();
      await firstRefresh.promise;
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });

    await act(async () => {
      trailingRefresh.resolve();
      await trailingRefresh.promise;
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(result.current.rows[0]?.id).toBe("trailing");

    await act(async () => {
      finalRefresh.resolve();
      await finalRefresh.promise;
    });

    await waitFor(() => expect(result.current.rows[0]?.id).toBe("final"));
    expect(fetchMock).toHaveBeenCalledTimes(4);

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("later"));
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("resets invalidation coalescing after a rejected refresh", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["initial"], 1))
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(makePage(["recovered"], 1));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("initial"));

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });
    await waitFor(() =>
      expect(result.current.error?.message).toBe("temporary failure"),
    );

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });

    await waitFor(() => expect(result.current.rows[0]?.id).toBe("recovered"));
    expect(result.current.error).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
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

  describe("pagination", () => {
    it("returns total + totalPages from the snapshot", async () => {
      // total is the count of all matching rows on the server, not the rows
      // returned in this page. totalPages = ceil(total / pageSize).
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [
              { id: "r1", data: {} },
              { id: "r2", data: {} },
            ],
            table_id: "tbl-uuid",
            total: 25,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.total).toBe(25);
      expect(result.current.totalPages).toBe(3);
      // Rows reflects only the page payload, not total.
      expect(result.current.rows).toHaveLength(2);
    });

    it("totalPages is 0 when total is 0 (unambiguous empty state)", async () => {
      // An empty table reports zero pages, not one. Callers driving
      // "Page 1 of N" UI can branch on `totalPages === 0` for the
      // empty-state rendering.
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [],
            table_id: "tbl-uuid",
            total: 0,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.total).toBe(0);
      expect(result.current.totalPages).toBe(0);
    });

    it("computes offset = (page - 1) * pageSize for page 3", async () => {
      // Page is 1-indexed, so page 3 with pageSize 10 means offset 20.
      // Verifies the snapshot POST body carries the right window.
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [],
            table_id: "tbl-uuid",
            total: 100,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() =>
        useTable("t1", { page: 3, pageSize: 10 }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));

      const reqBody = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(reqBody.offset).toBe(20);
      expect(reqBody.limit).toBe(10);
      expect(result.current.totalPages).toBe(10);
    });

    it("page change reissues the snapshot for the new window", async () => {
      // Verifies that bumping `page` re-runs the effect with the new
      // offset, not just shows stale rows from page 1.
      const fetchMock = vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              documents: [],
              table_id: "tbl-uuid",
              total: 100,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result, rerender } = renderHook(
        ({ page }: { page: number }) => useTable("t1", { page, pageSize: 10 }),
        { initialProps: { page: 1 } },
      );
      await waitFor(() => expect(result.current.loading).toBe(false));

      rerender({ page: 4 });
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

      const body1 = JSON.parse(fetchMock.mock.calls[0][1].body);
      const body2 = JSON.parse(fetchMock.mock.calls[1][1].body);
      expect(body1.offset).toBe(0);
      expect(body2.offset).toBe(30);
    });

    it("default page=1, pageSize=100 produces offset=0 limit=100", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [],
            table_id: "tbl-uuid",
            total: 0,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() => useTable("t1"));
      await waitFor(() => expect(result.current.loading).toBe(false));

      const reqBody = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(reqBody.offset).toBe(0);
      expect(reqBody.limit).toBe(100);
    });

    it("live insert that fits in the window appends, total bumps", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [{ id: "r1", data: {} }],
            table_id: "tbl-uuid",
            total: 1,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
      await waitFor(() => expect(result.current.loading).toBe(false));
      await waitFor(() => expect(lastOnEvent).not.toBeNull());

      act(() => {
        lastOnEvent?.({
          type: "document_change",
          action: "insert",
          row: { id: "r2", table_id: "tbl-uuid" },
          table_id: "tbl-uuid",
        });
      });

      expect(result.current.rows).toHaveLength(2);
      // total reflects the live insert so totalPages stays in sync without
      // a refetch.
      expect(result.current.total).toBe(2);
    });

    it("live insert that overflows the window trims the visible page", async () => {
      // Page 1 is full at pageSize=2. A live insert pushes off the trailing
      // row so the visible window stays at pageSize. The displaced row
      // appears on page 2 once the user navigates there (server-side, not
      // tracked here). total reflects the new count regardless.
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [
              { id: "r1", data: {} },
              { id: "r2", data: {} },
            ],
            table_id: "tbl-uuid",
            total: 2,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() => useTable("t1", { pageSize: 2 }));
      await waitFor(() => expect(result.current.loading).toBe(false));
      await waitFor(() => expect(lastOnEvent).not.toBeNull());

      act(() => {
        lastOnEvent?.({
          type: "document_change",
          action: "insert",
          row: { id: "r3", table_id: "tbl-uuid" },
          table_id: "tbl-uuid",
        });
      });

      expect(result.current.rows).toHaveLength(2);
      expect(result.current.total).toBe(3);
      // The first page kept its head (r1, r2); r3 is offscreen.
      expect(result.current.rows.map((r) => r.id)).toEqual(["r1", "r2"]);
    });

    it("live delete removes the row from the window and decrements total", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            documents: [
              { id: "r1", data: {} },
              { id: "r2", data: {} },
            ],
            table_id: "tbl-uuid",
            total: 2,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { result } = renderHook(() => useTable("t1", { pageSize: 10 }));
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
      expect(result.current.total).toBe(1);
    });
  });
});
