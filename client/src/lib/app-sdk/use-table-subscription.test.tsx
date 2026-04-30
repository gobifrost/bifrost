import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { tables } from "./tables";
import { useTableSubscription } from "./use-table-subscription";

describe("useTableSubscription", () => {
  it("subscribes on mount and unsubscribes on unmount", () => {
    const off = vi.fn();
    const subscribeSpy = vi.spyOn(tables, "subscribe").mockReturnValue(off);

    const onEvent = vi.fn();
    const { unmount } = renderHook(() =>
      useTableSubscription("table-uuid-1", onEvent),
    );

    expect(subscribeSpy).toHaveBeenCalledWith(
      "table-uuid-1",
      expect.any(Function),
    );
    unmount();
    expect(off).toHaveBeenCalled();

    subscribeSpy.mockRestore();
  });

  it("does not resubscribe when the callback changes but tableId stays the same", () => {
    const off = vi.fn();
    const subscribeSpy = vi.spyOn(tables, "subscribe").mockReturnValue(off);

    const onEvent1 = vi.fn();
    const onEvent2 = vi.fn();
    const { rerender, unmount } = renderHook(
      ({ cb }: { cb: (e: unknown) => void }) =>
        useTableSubscription("stable-id", cb as Parameters<typeof useTableSubscription>[1]),
      { initialProps: { cb: onEvent1 } },
    );

    expect(subscribeSpy).toHaveBeenCalledTimes(1);

    rerender({ cb: onEvent2 });
    // Still only one subscription — callback ref keeps it stable.
    expect(subscribeSpy).toHaveBeenCalledTimes(1);

    unmount();
    expect(off).toHaveBeenCalledTimes(1);

    subscribeSpy.mockRestore();
  });

  it("resubscribes when tableId changes", () => {
    const off = vi.fn();
    const subscribeSpy = vi.spyOn(tables, "subscribe").mockReturnValue(off);

    const onEvent = vi.fn();
    const { rerender, unmount } = renderHook(
      ({ id }: { id: string }) => useTableSubscription(id, onEvent),
      { initialProps: { id: "table-a" } },
    );

    expect(subscribeSpy).toHaveBeenCalledTimes(1);

    rerender({ id: "table-b" });
    // Old subscription torn down, new one started.
    expect(off).toHaveBeenCalledTimes(1);
    expect(subscribeSpy).toHaveBeenCalledTimes(2);

    unmount();
    subscribeSpy.mockRestore();
  });
});
