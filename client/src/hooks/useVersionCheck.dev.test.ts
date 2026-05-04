/**
 * Dev-mode behavior for useVersionCheck.
 *
 * When APP_VERSION is "unknown" (no VITE_BIFROST_VERSION baked in), the hook
 * must short-circuit: never fetch, never flip to true. We isolate this case
 * in its own file so the module-scope mock for `@/lib/version` can return
 * "unknown" without affecting the main suite.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/lib/version", () => ({ APP_VERSION: "unknown" }));

import { useVersionCheck } from "./useVersionCheck";

describe("useVersionCheck (dev / unknown version)", () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.useRealTimers();
	});

	it("never fires fetch and stays false forever", async () => {
		const fetchSpy = vi
			.spyOn(window, "fetch")
			.mockResolvedValue({ ok: true, json: async () => ({}) } as unknown as Response);

		const { result } = renderHook(() => useVersionCheck(60_000));

		await act(async () => {
			await vi.advanceTimersByTimeAsync(60_000 * 10);
		});

		expect(fetchSpy).not.toHaveBeenCalled();
		expect(result.current).toBe(false);
	});
});
