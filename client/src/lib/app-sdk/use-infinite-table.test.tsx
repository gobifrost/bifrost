import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const subscribeMock = vi.fn();
let lastOnEvent:
  | ((evt: Record<string, unknown>) => void)
  | null = null;
let lastOnReconnect: (() => void) | null = null;
let onEvents: Array<(evt: Record<string, unknown>) => void> = [];

vi.mock("./ws-client", () => ({
  subscribeToTable: (
    _tableId: string,
    _filter: unknown,
    cb: (evt: Record<string, unknown>) => void,
    onReconnect?: () => void,
  ) => {
    lastOnEvent = cb;
    lastOnReconnect = onReconnect ?? null;
    onEvents.push(cb);
    subscribeMock(_tableId, _filter, cb);
    return () => {
      if (lastOnEvent === cb) lastOnEvent = null;
      if (lastOnReconnect === onReconnect) lastOnReconnect = null;
    };
  },
}));

import { useInfiniteTable } from "./use-infinite-table";

function makePage(ids: string[], total: number, table_id = "tbl-uuid") {
  return new Response(
    JSON.stringify({
      documents: ids.map((id) => ({ id, data: {} })),
      table_id,
      total,
    }),
    {
      status: 200,
      headers: { "content-type": "application/json" },
    },
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

describe("useInfiniteTable", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    subscribeMock.mockClear();
    lastOnEvent = null;
    lastOnReconnect = null;
    onEvents = [];
  });

  it("loads the first page with skip_count omitted (server returns count)", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makePage(["a", "b"], 2));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 100 }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.skip_count).toBeUndefined();
    expect(body.limit).toBe(100);
    expect(body.offset).toBe(0);
    expect(result.current.rows).toHaveLength(2);
  });

  it("loadMore appends the next page with skip_count: true", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["a", "b"], 4))
      .mockResolvedValueOnce(makePage(["c", "d"], 4));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 2 }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    const secondBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(secondBody.skip_count).toBe(true);
    expect(secondBody.offset).toBe(2);
    expect(result.current.rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("refreshes all loaded rows after a subscription reconnect", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["a", "b"], 4))
      .mockResolvedValueOnce(makePage(["c", "d"], 4))
      .mockResolvedValueOnce(makePage(["a", "b", "c", "changed"], 4));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 2 }),
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    await act(async () => result.current.loadMore());
    expect(result.current.rows.map((row) => row.id)).toEqual(["a", "b", "c", "d"]);

    act(() => lastOnReconnect?.());

    await waitFor(() =>
      expect(result.current.rows.map((row) => row.id)).toEqual([
        "a",
        "b",
        "c",
        "changed",
      ]),
    );
  });

  it("refreshes the loaded window after table invalidation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["a", "b"], 4))
      .mockResolvedValueOnce(makePage(["c", "d"], 4))
      .mockResolvedValueOnce(makePage(["fresh-a", "fresh-b", "fresh-c"], 3));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", {
        where: { status: "active" },
        pageSize: 2,
        order_by: "created_at",
        order_dir: "desc",
        scope: "org-a",
      }),
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    await act(async () => result.current.loadMore());
    expect(result.current.rows.map((row) => row.id)).toEqual(["a", "b", "c", "d"]);

    act(() => {
      lastOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });

    await waitFor(() =>
      expect(result.current.rows.map((row) => row.id)).toEqual([
        "fresh-a",
        "fresh-b",
        "fresh-c",
      ]),
    );
    expect(result.current.hasMore).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      where: { status: "active" },
      limit: 4,
      offset: 0,
      order_by: "created_at",
      order_dir: "desc",
    });
    expect(fetchMock.mock.calls[2][0]).toMatch(/scope=org-a$/);
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

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 10 }),
    );
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

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 10 }),
    );
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

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 10 }),
    );
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

  it("ignores stale invalidation refresh results after query changes", async () => {
    const staleRefresh = deferredPage(["stale"], 1);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["old-scope"], 1))
      .mockReturnValueOnce(staleRefresh.promise)
      .mockResolvedValueOnce(makePage(["new-scope"], 1, "tbl-new"));
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ scope }: { scope: string }) =>
        useInfiniteTable("t1", { pageSize: 10, scope }),
      { initialProps: { scope: "org-a" } },
    );
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("old-scope"));
    const oldOnEvent = onEvents[0];

    act(() => {
      oldOnEvent?.({ type: "table_invalidated", table_id: "tbl-uuid" });
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    rerender({ scope: "org-b" });
    await waitFor(() => expect(result.current.rows[0]?.id).toBe("new-scope"));

    await act(async () => {
      staleRefresh.resolve();
      await staleRefresh.promise;
    });

    expect(result.current.rows[0]?.id).toBe("new-scope");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("hasMore flips false when a partial page comes back", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makePage(["a", "b"], 3))
      .mockResolvedValueOnce(makePage(["c"], 3));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 2 }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.hasMore).toBe(false);
    expect(result.current.rows).toHaveLength(3);
  });

  it("subscribes once with the compiled filter", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makePage([], 0));
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() =>
      useInfiniteTable("t1", { where: { status: "active" }, pageSize: 100 }),
    );
    await waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1));

    const [tableId, filter] = subscribeMock.mock.calls[0];
    expect(tableId).toBe("tbl-uuid");
    expect(filter).toEqual({ eq: [{ row: "status" }, "active"] });
  });

  it("surfaces subscribe error frames as `error`", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makePage([], 0));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { pageSize: 100 }),
    );
    await waitFor(() => expect(lastOnEvent).not.toBeNull());

    act(() => {
      lastOnEvent?.({ type: "error", message: "Access denied" });
    });

    expect(result.current.error?.message).toBe("Access denied");
  });

  it("rejects unsupported operators (contains/starts_with/...) before fetching", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useInfiniteTable("t1", { where: { name: { contains: "x" } } }),
    );
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.message).toContain("contains");
    // Snapshot did fire (loadMore was called) — but the subsequent subscribe
    // step never ran because compileFilterToExpr threw before subscribe.
  });

  describe("live events keep the loaded rows in server order", () => {
    function page(docs: Array<{ id: string; name?: string }>, total: number) {
      return new Response(
        JSON.stringify({
          documents: docs.map(({ id, ...data }) => ({ id, data })),
          table_id: "tbl-uuid",
          total,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    function emit(evt: Record<string, unknown>) {
      act(() => {
        lastOnEvent?.({ type: "document_change", table_id: "tbl-uuid", ...evt });
      });
    }

    function insert(row: Record<string, unknown>) {
      emit({ action: "insert", row: { table_id: "tbl-uuid", ...row } });
    }

    async function mount(
      fetchMock: ReturnType<typeof vi.fn>,
      query: Parameters<typeof useInfiniteTable>[1],
    ) {
      vi.stubGlobal("fetch", fetchMock);
      const hook = renderHook(() => useInfiniteTable("t1", query));
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      await waitFor(() => expect(lastOnEvent).not.toBeNull());
      return hook;
    }

    const ids = (rows: Array<{ id: string }>) => rows.map((r) => r.id);
    const offsetOf = (fetchMock: ReturnType<typeof vi.fn>, call: number) =>
      JSON.parse(fetchMock.mock.calls[call][1].body).offset;

    it("places an insert at its sorted position and shifts the next page's offset", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "a", name: "Alpha" }, { id: "c", name: "Charlie" }], 4))
        .mockResolvedValueOnce(page([{ id: "d", name: "Delta" }], 5));
      const { result } = await mount(fetchMock, { order_by: "name", pageSize: 2 });

      insert({ id: "b", name: "Bravo" });
      expect(ids(result.current.rows)).toEqual(["a", "b", "c"]);

      await act(async () => {
        await result.current.loadMore();
      });
      expect(offsetOf(fetchMock, 1)).toBe(3);
      expect(ids(result.current.rows)).toEqual(["a", "b", "c", "d"]);
    });

    it("leaves an insert sorting past the loaded rows for loadMore when more pages remain", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "a", name: "Alpha" }, { id: "b", name: "Bravo" }], 4))
        .mockResolvedValueOnce(page([{ id: "c", name: "Charlie" }, { id: "z", name: "Zulu" }], 5));
      const { result } = await mount(fetchMock, { order_by: "name", pageSize: 2 });

      insert({ id: "z", name: "Zulu" });
      expect(ids(result.current.rows)).toEqual(["a", "b"]);

      await act(async () => {
        await result.current.loadMore();
      });
      expect(offsetOf(fetchMock, 1)).toBe(2);
      expect(ids(result.current.rows)).toEqual(["a", "b", "c", "z"]);
    });

    it("appends an insert sorting past the loaded rows once everything is loaded", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "a", name: "Alpha" }], 1));
      const { result } = await mount(fetchMock, { order_by: "name", pageSize: 2 });
      expect(result.current.hasMore).toBe(false);

      insert({ id: "z", name: "Zulu" });
      expect(ids(result.current.rows)).toEqual(["a", "z"]);
    });

    it("without order_by, does not append a new row while more pages remain", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "a" }, { id: "b" }], 3))
        .mockResolvedValueOnce(page([{ id: "c" }, { id: "new" }], 4));
      const { result } = await mount(fetchMock, { pageSize: 2 });

      insert({ id: "new" });
      expect(ids(result.current.rows)).toEqual(["a", "b"]);

      await act(async () => {
        await result.current.loadMore();
      });
      expect(ids(result.current.rows)).toEqual(["a", "b", "c", "new"]);
    });

    it("without order_by, desc puts a new row first", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "b" }, { id: "a" }], 4));
      const { result } = await mount(fetchMock, { order_dir: "desc", pageSize: 2 });

      insert({ id: "c" });
      expect(ids(result.current.rows)).toEqual(["c", "b", "a"]);
    });

    it("a delete of a loaded row shifts the next page's offset back", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "a", name: "Alpha" }, { id: "b", name: "Bravo" }], 4))
        .mockResolvedValueOnce(page([{ id: "c", name: "Charlie" }, { id: "d", name: "Delta" }], 3));
      const { result } = await mount(fetchMock, { order_by: "name", pageSize: 2 });

      emit({ action: "delete", row_id: "a" });
      expect(ids(result.current.rows)).toEqual(["b"]);

      await act(async () => {
        await result.current.loadMore();
      });
      expect(offsetOf(fetchMock, 1)).toBe(1);
      expect(ids(result.current.rows)).toEqual(["b", "c", "d"]);
    });

    it("loadMore never shows a row twice", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(page([{ id: "a" }, { id: "b" }], 4))
        .mockResolvedValueOnce(page([{ id: "b" }, { id: "c" }], 4));
      const { result } = await mount(fetchMock, { pageSize: 2 });

      await act(async () => {
        await result.current.loadMore();
      });
      expect(ids(result.current.rows)).toEqual(["a", "b", "c"]);
    });

    it("moves an updated row when its sort key changes, and applies a duplicate insert the same way", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(
          page([{ id: "a", name: "Alpha" }, { id: "b", name: "Bravo" }, { id: "c", name: "Charlie" }], 3),
        );
      const { result } = await mount(fetchMock, { order_by: "name", pageSize: 5 });

      emit({ action: "update", row: { table_id: "tbl-uuid", id: "a", name: "Delta" } });
      expect(ids(result.current.rows)).toEqual(["b", "c", "a"]);

      insert({ id: "b", name: "Echo" });
      expect(ids(result.current.rows)).toEqual(["c", "a", "b"]);
    });
  });
});
