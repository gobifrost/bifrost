/**
 * Tests for useVersionCheck.
 *
 * Covers polling cadence, visibility pause/resume, network error swallowing,
 * and cleanup. The "skip in dev" case (APP_VERSION === "unknown") lives in
 * useVersionCheck.dev.test.ts because it needs a different module-scope mock.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/lib/version", () => ({ APP_VERSION: "v1.0.0" }));

import { useVersionCheck } from "./useVersionCheck";

function mockFetchOnce(version: string, ok = true) {
	return vi.spyOn(window, "fetch").mockResolvedValue({
		ok,
		json: async () => ({ version }),
	} as unknown as Response);
}

function setVisibility(state: "visible" | "hidden") {
	Object.defineProperty(document, "visibilityState", {
		configurable: true,
		get: () => state,
	});
}

describe("useVersionCheck", () => {
	beforeEach(() => {
		vi.useFakeTimers();
		setVisibility("visible");
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.useRealTimers();
	});

	it("returns false when versions match", async () => {
		mockFetchOnce("v1.0.0");
		const { result } = renderHook(() => useVersionCheck(60_000));

		// Let the immediate-fire fetch promise resolve.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});

		expect(result.current).toBe(false);
	});

	it("returns true when versions differ", async () => {
		mockFetchOnce("v2.0.0");
		const { result } = renderHook(() => useVersionCheck(60_000));

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});

		expect(result.current).toBe(true);
	});

	it("pauses polling while the tab is hidden", async () => {
		const fetchSpy = mockFetchOnce("v1.0.0");
		setVisibility("hidden");
		renderHook(() => useVersionCheck(60_000));

		// Immediate fire is gated on visibility — it bails before fetching.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(fetchSpy).not.toHaveBeenCalled();

		// Advance past the polling interval — still hidden, still no fetch.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(60_000);
		});
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it("re-fires when visibility returns to visible", async () => {
		const fetchSpy = mockFetchOnce("v1.0.0");
		setVisibility("hidden");
		renderHook(() => useVersionCheck(60_000));

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(fetchSpy).not.toHaveBeenCalled();

		// Flip to visible and dispatch the event the hook listens for.
		setVisibility("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			await vi.advanceTimersByTimeAsync(0);
		});

		expect(fetchSpy).toHaveBeenCalledTimes(1);
	});

	it("swallows fetch rejections without surfacing an update", async () => {
		const fetchSpy = vi
			.spyOn(window, "fetch")
			.mockRejectedValue(new Error("network down"));
		const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

		const { result } = renderHook(() => useVersionCheck(60_000));

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});

		expect(fetchSpy).toHaveBeenCalled();
		expect(result.current).toBe(false);
		expect(errorSpy).not.toHaveBeenCalled();
	});

	it("polls again after the interval elapses", async () => {
		const fetchSpy = mockFetchOnce("v1.0.0");
		renderHook(() => useVersionCheck(60_000));

		// Immediate fire.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(fetchSpy).toHaveBeenCalledTimes(1);

		// One full interval later, second poll.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(60_000);
		});
		expect(fetchSpy).toHaveBeenCalledTimes(2);
	});

	it("stops polling and removes the listener after unmount", async () => {
		const fetchSpy = mockFetchOnce("v1.0.0");
		const { unmount } = renderHook(() => useVersionCheck(60_000));

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(fetchSpy).toHaveBeenCalledTimes(1);

		unmount();

		// Pending interval should be cancelled — no further fetches.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(60_000 * 5);
		});
		expect(fetchSpy).toHaveBeenCalledTimes(1);

		// Visibility events post-unmount must also be ignored.
		setVisibility("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(fetchSpy).toHaveBeenCalledTimes(1);
	});
});
