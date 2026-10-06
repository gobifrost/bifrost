import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { ProductUpdatesSidebarLink } from "./ProductUpdatesSidebarLink";

const state = vi.hoisted(() => ({ readIds: new Set<string>() }));

vi.mock("@/lib/product-updates-preview", () => ({
	PRODUCT_UPDATES_RECEIPTS_EVENT: "bifrost:product-updates-receipts",
	productUpdatesPreviewAdapter: {
		getBundle: vi.fn(async () => ({ entries: [{ id: "entry-1" }] })),
		getReadEntryIds: vi.fn(async () => state.readIds),
	},
}));

vi.mock("./sidebarLinks", () => ({
	SidebarLink: ({ children }: { children: ReactNode }) => (
		<div>{children}</div>
	),
}));

describe("ProductUpdatesSidebarLink", () => {
	beforeEach(() => {
		state.readIds = new Set();
	});

	it("refreshes the quiet unread cue from same-window and cross-tab receipt events", async () => {
		renderWithProviders(
			<ProductUpdatesSidebarLink adminId="admin-a" isCollapsed={false} />,
		);
		expect(
			await screen.findByLabelText("1 unread product updates"),
		).toBeVisible();

		state.readIds = new Set(["entry-1"]);
		window.dispatchEvent(new Event("bifrost:product-updates-receipts"));
		await waitFor(() =>
			expect(
				screen.queryByLabelText("1 unread product updates"),
			).not.toBeInTheDocument(),
		);

		state.readIds = new Set();
		window.dispatchEvent(new StorageEvent("storage"));
		await waitFor(() =>
			expect(
				screen.getByLabelText("1 unread product updates"),
			).toBeVisible(),
		);
	});
});
