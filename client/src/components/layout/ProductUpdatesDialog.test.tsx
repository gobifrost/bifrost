import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import type { ProductUpdatesAdapter } from "@/lib/product-updates-preview";
import type { ProductUpdatesBundle } from "@/generated/product-updates";
import { ProductUpdatesDialog } from "./ProductUpdatesDialog";

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <svg aria-hidden="true" />,
}));
const bundle: ProductUpdatesBundle = {
	schema_version: 1,
	target_ref: "abc",
	content_ref: "abc",
	other_changes: [],
	entries: ["seen", "new"].map((id) => ({
		id,
		revision: 1,
		title: `${id} update`,
		published_at: "2026-10-06T12:00:00Z",
		markdown: "Release details.",
		area: "Platform",
		type: "New",
		action_required: false,
		sources: [],
		contributors: [],
		assets: [],
	})),
};
function adapter(
	overrides: Partial<ProductUpdatesAdapter> = {},
): ProductUpdatesAdapter {
	return {
		getBundle: vi.fn(async () => bundle),
		getReadEntryIds: vi.fn(async () => new Set(["seen"])),
		markRead: vi.fn(async () => {}),
		...overrides,
	};
}
describe("ProductUpdatesDialog", () => {
	it("automatically presents and acknowledges only unseen bundled entries, then keeps history reachable", async () => {
		const source = adapter();
		const { user } = renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		const dialog = await screen.findByRole("dialog", {
			name: "What's New",
		});
		expect(
			within(dialog).getByRole("heading", { name: "new update" }),
		).toBeVisible();
		expect(
			within(dialog).queryByRole("heading", { name: "seen update" }),
		).not.toBeInTheDocument();
		await waitFor(() =>
			expect(source.markRead).toHaveBeenCalledWith("admin-a", ["new"]),
		);
		expect(
			within(dialog).getByRole("link", { name: "View All Updates" }),
		).toHaveAttribute("href", "/whats-new");
		await user.keyboard("{Escape}");
		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(source.getBundle).toHaveBeenCalledTimes(1);
	});
	it("stays quiet when every entry has already been presented", async () => {
		const source = adapter({
			getReadEntryIds: vi.fn(async () => new Set(["seen", "new"])),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getReadEntryIds).toHaveBeenCalled());
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(source.markRead).not.toHaveBeenCalled();
	});

	it("does not acknowledge a failed load", async () => {
		const source = adapter({
			getBundle: vi.fn(async () => {
				throw new Error("offline");
			}),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getBundle).toHaveBeenCalled());
		expect(source.markRead).not.toHaveBeenCalled();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
	it("keeps the displayed updates usable when acknowledgment fails", async () => {
		const source = adapter({
			markRead: vi.fn(async () => {
				throw new Error("denied");
			}),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		expect(
			await screen.findByText(/couldn't be saved as seen/),
		).toBeVisible();
		expect(
			screen.getByRole("heading", { name: "new update" }),
		).toBeVisible();
	});
	it("leaves direct history visits unobstructed", () => {
		const source = adapter();
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
			{ initialEntries: ["/whats-new"] },
		);
		expect(source.getBundle).not.toHaveBeenCalled();
		expect(source.markRead).not.toHaveBeenCalled();
	});
});
