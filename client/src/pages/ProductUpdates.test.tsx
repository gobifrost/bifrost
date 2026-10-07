import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import type {
	ProductUpdatesAdapter,
	ProductUpdatesBundle,
} from "@/services/productUpdates";
import { ProductUpdates } from "./ProductUpdates";

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <svg aria-hidden="true" />,
}));

const bundle: ProductUpdatesBundle = {
	schema_version: 1,
	target_ref: "abc123",
	content_ref: "approved-release",
	other_changes: [],
	entries: [
		{
			id: "entry-1",
			revision: 1,
			published_at: "2026-10-06T22:06:01Z",
			title: "Readable Update",
			markdown: "A reviewed update.",
			area: "Platform",
			type: "New",
			action_required: false,
			sources: [],
			contributors: [],
			assets: [],
		},
	],
};

function adapter(
	overrides: Partial<ProductUpdatesAdapter> = {},
): ProductUpdatesAdapter {
	return {
		getFeed: vi.fn(async () => ({ bundle, seen_entry_ids: [] }) as never),
		acknowledge: vi.fn(async () => ({ seen_entry_ids: [] }) as never),
		...overrides,
	};
}

describe("ProductUpdates", () => {
	it("renders the authorized feed and acknowledges its presented UUIDs", async () => {
		const source = adapter();
		renderWithProviders(<ProductUpdates adapter={source} />);

		expect(
			await screen.findByRole("heading", { name: "Readable Update" }),
		).toBeVisible();
		await waitFor(() =>
			expect(source.acknowledge).toHaveBeenCalledWith(["entry-1"]),
		);
		expect(screen.getByRole("link", { name: "Done" })).toHaveAttribute(
			"href",
			"/",
		);
	});

	it("keeps the feed visible and reports a receipt persistence failure", async () => {
		const source = adapter({
			acknowledge: vi.fn(async () => {
				throw new Error("offline");
			}),
		});
		renderWithProviders(<ProductUpdates adapter={source} />);

		expect(
			await screen.findByText("Seen State Wasn't Saved"),
		).toBeVisible();
		expect(
			screen.getByRole("heading", { name: "Readable Update" }),
		).toBeVisible();
	});

	it("reports feed failures without creating a receipt", async () => {
		const source = adapter({
			getFeed: vi.fn(async () => {
				throw new Error("offline");
			}),
		});
		renderWithProviders(<ProductUpdates adapter={source} />);

		expect(
			await screen.findByText("Couldn't Load Product Updates"),
		).toBeVisible();
		expect(source.acknowledge).not.toHaveBeenCalled();
	});

	it("does not send an invalid empty receipt when the feed has no visible entries", async () => {
		const source = adapter({
			getFeed: vi.fn(
				async () =>
					({
						bundle: { ...bundle, entries: [] },
						seen_entry_ids: [],
					}) as never,
			),
		});
		renderWithProviders(<ProductUpdates adapter={source} />);

		expect(await screen.findByText("No Updates Yet")).toBeVisible();
		expect(source.acknowledge).not.toHaveBeenCalled();
	});
});
