/**
 * Tests for the vite:preloadError handler.
 *
 * The handler reloads once on a preload error, but suppresses subsequent
 * reloads within a 5s window so a chronically broken deploy can't trap
 * the user in a reload tornado.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { handleVitePreloadError } from "./preload-error-handler";

const RELOAD_KEY = "bifrost:last-preload-reload";

describe("handleVitePreloadError", () => {
	let reload: ReturnType<typeof vi.fn>;
	let originalLocation: Location;
	let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		sessionStorage.clear();

		// Mirror the VersionUpdateBanner test pattern: replace window.location
		// with a copy whose `reload` is a spy so we can assert without
		// actually navigating jsdom.
		reload = vi.fn();
		originalLocation = window.location;
		Object.defineProperty(window, "location", {
			configurable: true,
			value: { ...originalLocation, reload },
		});

		consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
	});

	afterEach(() => {
		Object.defineProperty(window, "location", {
			configurable: true,
			value: originalLocation,
		});
		consoleErrorSpy.mockRestore();
		sessionStorage.clear();
	});

	it("first preload error reloads and stores timestamp", () => {
		const before = Date.now();
		handleVitePreloadError();
		const after = Date.now();

		expect(reload).toHaveBeenCalledTimes(1);

		const stored = sessionStorage.getItem(RELOAD_KEY);
		expect(stored).not.toBeNull();
		const ts = Number(stored);
		expect(ts).toBeGreaterThanOrEqual(before);
		expect(ts).toBeLessThanOrEqual(after);
	});

	it("second preload error within 5s is suppressed (no reload)", () => {
		// Simulate a reload that just happened 1s ago.
		sessionStorage.setItem(RELOAD_KEY, String(Date.now() - 1000));

		handleVitePreloadError();

		expect(reload).not.toHaveBeenCalled();
		expect(consoleErrorSpy).toHaveBeenCalledWith(
			"[bifrost] preload error after recent reload, suppressing",
		);
	});

	it("preload error after the 5s window reloads again", () => {
		// Simulate a reload from 6s ago — outside the loop guard.
		sessionStorage.setItem(RELOAD_KEY, String(Date.now() - 6000));

		handleVitePreloadError();

		expect(reload).toHaveBeenCalledTimes(1);
	});
});
