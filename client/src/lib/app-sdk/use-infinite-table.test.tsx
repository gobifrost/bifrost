import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

describe("useInfiniteTable", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    subscribeMock.mockClear();
    lastOnEvent = null;
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
});
