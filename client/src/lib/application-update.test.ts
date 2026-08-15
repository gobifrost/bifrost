import { afterEach, describe, expect, it, vi } from "vitest";

import {
	getApplicationUpdateInProgress,
	requestApplicationReload,
	subscribeToApplicationUpdate,
} from "./application-update";

describe("requestApplicationReload", () => {
	afterEach(() => {
		vi.useRealTimers();
		sessionStorage.clear();
	});

	it("publishes the update state before reloading and honors the loop guard", () => {
		vi.useFakeTimers();
		const reload = vi.fn();
		const originalLocation = window.location;
		Object.defineProperty(window, "location", {
			configurable: true,
			value: { ...originalLocation, reload },
		});
		const listener = vi.fn();
		const unsubscribe = subscribeToApplicationUpdate(listener);

		expect(requestApplicationReload("test-update", 5_000)).toBe(true);
		expect(getApplicationUpdateInProgress()).toBe(true);
		expect(listener).toHaveBeenCalledTimes(1);
		expect(reload).not.toHaveBeenCalled();

		vi.runAllTimers();
		expect(reload).toHaveBeenCalledTimes(1);
		expect(requestApplicationReload("test-update", 5_000)).toBe(false);

		unsubscribe();
		Object.defineProperty(window, "location", {
			configurable: true,
			value: originalLocation,
		});
	});
});
