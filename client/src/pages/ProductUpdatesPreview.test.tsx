import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";
import type { ProductUpdatesAdapter } from "@/lib/product-updates-preview";
import type { ProductUpdatesBundle } from "@/generated/product-updates";
import { ProductUpdatesPreview } from "./ProductUpdatesPreview";

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <svg aria-hidden="true" />,
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { id: "current-admin" } }),
}));

const bundle: ProductUpdatesBundle = {
	schema_version: 1,
	target_ref: "abc123",
	content_ref: "draft-backfill",
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
		getBundle: vi.fn(async () => bundle),
		getReadEntryIds: vi.fn(async () => new Set<string>()),
		markRead: vi.fn(async () => {}),
		...overrides,
	};
}

describe("ProductUpdatesPreview", () => {
	it("labels the development feed as a draft preview and keeps its public links available", async () => {
		renderWithProviders(<ProductUpdatesPreview adapter={adapter()} />, {
			initialEntries: ["/?admin=admin-a"],
		});
		await screen.findByRole("heading", { name: "Readable Update" });

		expect(
			screen.getByText("Preview · Draft Backfill"),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute(
			"href",
			expect.stringContaining("github.com"),
		);
		expect(screen.getByRole("link", { name: "Website" })).toHaveAttribute(
			"href",
			"https://gobifrost.com",
		);
		expect(screen.getByRole("link", { name: "Discord" })).toHaveAttribute(
			"href",
			"https://discord.gg/f7TCcWX2s",
		);
	});

	it("marks only the presented UUIDs read for the selected admin", async () => {
		const previewAdapter = adapter();
		renderWithProviders(
			<ProductUpdatesPreview adapter={previewAdapter} />,
			{ initialEntries: ["/?admin=admin-a"] },
		);

		await screen.findByRole("heading", { name: "Readable Update" });

		await waitFor(() =>
			expect(previewAdapter.markRead).toHaveBeenCalledWith("admin-a", [
				"entry-1",
			]),
		);
		expect(
			screen.queryByRole("button", { name: /Mark.*Read/ }),
		).not.toBeInTheDocument();
		expect(screen.queryByRole("tab")).not.toBeInTheDocument();
		expect(screen.queryByText("Unread")).not.toBeInTheDocument();
	});

	it("keeps updates available and reports an automatic receipt failure", async () => {
		const previewAdapter = adapter({
			markRead: vi.fn(async () => {
				throw new Error("storage denied");
			}),
		});
		renderWithProviders(
			<ProductUpdatesPreview adapter={previewAdapter} />,
			{ initialEntries: ["/?admin=admin-a"] },
		);

		await screen.findByRole("heading", { name: "Readable Update" });

		expect(
			await screen.findByText("Seen State Wasn't Saved"),
		).toBeVisible();
		expect(
			screen.getByRole("heading", { name: "Readable Update" }),
		).toBeVisible();
	});

	it("recovers from the failure fixture when switching back to normal", async () => {
		const { user } = renderWithProviders(
			<ProductUpdatesPreview adapter={adapter()} />,
			{ initialEntries: ["/?state=failure"] },
		);

		expect(
			await screen.findByText("Couldn't Load Product Updates"),
		).toBeVisible();
		await user.click(screen.getByText("Preview Controls"));
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Preview state" }),
			"normal",
		);

		expect(
			await screen.findByRole("heading", { name: "Readable Update" }),
		).toBeVisible();
		expect(
			screen.queryByText("Couldn't Load Product Updates"),
		).not.toBeInTheDocument();
	});

	it("renders a direct commit source and an honest image error", async () => {
		const entry = {
			...bundle.entries[0],
			markdown: "![Roles page](assets/entry-1/roles.png)",
			sources: [{ commit: "abcdef0123456789" }],
			assets: [
				{
					path: "assets/entry-1/roles.png",
					url: "/missing-roles.png",
					alt: "Roles page",
				},
			],
		};
		const previewAdapter = adapter({
			getBundle: vi.fn(async () => ({ ...bundle, entries: [entry] })),
		});
		renderWithProviders(<ProductUpdatesPreview adapter={previewAdapter} />);

		const image = await screen.findByRole("img", { name: "Roles page" });
		expect(screen.getByRole("link", { name: "abcdef0" })).toHaveAttribute(
			"href",
			"https://github.com/gobifrost/bifrost/commit/abcdef0123456789",
		);
		fireEvent.error(image);
		expect(
			await screen.findByText("Screenshot unavailable: Roles page"),
		).toBeVisible();
	});
});
