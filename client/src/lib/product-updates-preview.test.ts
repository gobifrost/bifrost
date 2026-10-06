import { describe, expect, it, vi } from "vitest";
import type { ProductUpdatesBundle } from "@/generated/product-updates";
import { createProductUpdatesPreviewAdapter } from "./product-updates-preview";

const bundle: ProductUpdatesBundle = {
	schema_version: 1,
	target_ref: "abc123",
	content_ref: "draft-backfill",
	entries: [
		{
			id: "dev-only-entry-uuid",
			revision: 1,
			published_at: "2026-10-07T22:06:01Z",
			title: "Development Update",
			markdown: "Development.",
			area: "Platform",
			type: "New",
			action_required: false,
			sources: [],
			contributors: [],
			assets: [],
		},
		{
			id: "stable-entry-uuid",
			revision: 1,
			published_at: "2026-10-06T22:06:01Z",
			title: "Retained Update",
			markdown: "Retained.",
			area: "Platform",
			type: "New",
			action_required: false,
			sources: [],
			contributors: [],
			assets: [],
		},
	],
	other_changes: [],
};

describe("createProductUpdatesPreviewAdapter", () => {
	it("keeps UUID receipts independent per admin while dev and stable use the same entry identity", async () => {
		const values = new Map<string, string>();
		const storage = {
			getItem: vi.fn((key: string) => values.get(key) ?? null),
			setItem: vi.fn((key: string, value: string) =>
				values.set(key, value),
			),
		};
		const adapter = createProductUpdatesPreviewAdapter(bundle, storage);
		const sameWindowAdapter = createProductUpdatesPreviewAdapter(
			bundle,
			storage,
		);

		await adapter.markRead("admin-a", ["stable-entry-uuid"]);

		expect(await adapter.getReadEntryIds("admin-a")).toEqual(
			new Set(["stable-entry-uuid"]),
		);
		expect(await adapter.getReadEntryIds("admin-b")).toEqual(new Set());
		expect(await sameWindowAdapter.getReadEntryIds("admin-a")).toEqual(
			new Set(["stable-entry-uuid"]),
		);
		const devBundle = await adapter.getBundle("dev");
		const stableBundle = await adapter.getBundle("stable");
		expect(devBundle).not.toBe(stableBundle);
		expect(devBundle.entries.map((entry) => entry.id)).toEqual([
			"dev-only-entry-uuid",
			"stable-entry-uuid",
		]);
		expect(stableBundle.entries.map((entry) => entry.id)).toEqual([
			"stable-entry-uuid",
		]);
	});
});
