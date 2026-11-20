import { afterEach, describe, expect, it, vi } from "vitest";

const storageKey = "bifrost-presentation-mode";

afterEach(() => localStorage.removeItem(storageKey));

async function loadStore() {
	vi.resetModules();
	return (await import("./presentationStore")).usePresentationStore;
}

describe("presentation mode", () => {
	it("defaults off and restores both settings after reload", async () => {
		const store = await loadStore();
		expect(store.getState().enabled).toBe(false);

		store.getState().toggle();
		const reloaded = await loadStore();
		expect(reloaded.getState().enabled).toBe(true);

		reloaded.getState().setEnabled(false);
		expect((await loadStore()).getState().enabled).toBe(false);
	});

	it("persists opt-in from the URL and ignores opt-out parameters", async () => {
		const store = await loadStore();
		store.getState().enableFromSearch("?demo=1");
		expect(store.getState().enabled).toBe(false);

		store.getState().enableFromSearch("?presentation=1");
		const reloaded = await loadStore();
		reloaded.getState().enableFromSearch("");
		reloaded.getState().enableFromSearch("?presentation=0");
		expect(reloaded.getState().enabled).toBe(true);
	});
});
