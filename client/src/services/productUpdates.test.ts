import { beforeEach, describe, expect, it, vi } from "vitest";

const apiClient = vi.hoisted(() => ({
	GET: vi.fn(),
	POST: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({ apiClient }));

import { acknowledgeProductUpdates, getProductUpdates } from "./productUpdates";

describe("product updates service", () => {
	beforeEach(() => {
		apiClient.GET.mockReset();
		apiClient.POST.mockReset();
	});

	it("loads the authenticated feed with the server-owned seen state", async () => {
		const feed = { bundle: { entries: [] }, seen_entry_ids: ["entry-1"] };
		apiClient.GET.mockResolvedValue({ data: feed });

		await expect(getProductUpdates()).resolves.toBe(feed);
		expect(apiClient.GET).toHaveBeenCalledWith("/api/product-updates");
	});

	it("acknowledges exactly the entries that were presented", async () => {
		const receipt = { seen_entry_ids: ["entry-1"] };
		apiClient.POST.mockResolvedValue({ data: receipt });

		await expect(acknowledgeProductUpdates(["entry-1"])).resolves.toBe(
			receipt,
		);
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/product-updates/receipts",
			{ body: { entry_ids: ["entry-1"] } },
		);
	});

	it("surfaces API errors without claiming acknowledgement persisted", async () => {
		apiClient.POST.mockResolvedValue({ error: { detail: "Feed changed" } });

		await expect(acknowledgeProductUpdates(["entry-1"])).rejects.toThrow(
			"Feed changed",
		);
	});
});
