import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import {
	act,
	renderWithProviders,
	screen,
	waitFor,
	within,
} from "@/test-utils";
import type {
	ProductUpdatesAdapter,
	ProductUpdatesBundle,
} from "@/services/productUpdates";
import { ProductUpdatesDialog } from "./ProductUpdatesDialog";

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <svg aria-hidden="true" />,
}));

let channels: BroadcastChannelMock[] = [];

class BroadcastChannelMock {
	onmessage: ((event: MessageEvent<unknown>) => void) | null = null;

	constructor(_name: string) {
		channels.push(this);
	}

	postMessage = vi.fn();
	close = vi.fn();
}

beforeEach(() => {
	channels = [];
	vi.stubGlobal("BroadcastChannel", BroadcastChannelMock);
});

afterEach(() => {
	vi.unstubAllGlobals();
});
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
		getFeed: vi.fn(
			async () => ({ bundle, seen_entry_ids: ["seen"] }) as never,
		),
		acknowledge: vi.fn(async () => ({ seen_entry_ids: [] }) as never),
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
			expect(source.acknowledge).toHaveBeenCalledWith(["new"]),
		);
		expect(
			within(dialog).getByRole("link", { name: "View All Updates" }),
		).toHaveAttribute("href", "/whats-new");
		await user.click(within(dialog).getByRole("button", { name: "Done" }));
		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(source.getFeed).toHaveBeenCalledTimes(1);
	});
	it("stays quiet when every entry has already been presented", async () => {
		const source = adapter({
			getFeed: vi.fn(
				async () =>
					({ bundle, seen_entry_ids: ["seen", "new"] }) as never,
			),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getFeed).toHaveBeenCalled());
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(source.acknowledge).not.toHaveBeenCalled();
	});

	it("does not acknowledge a failed load", async () => {
		const source = adapter({
			getFeed: vi.fn(async () => {
				throw new Error("offline");
			}),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getFeed).toHaveBeenCalled());
		expect(source.acknowledge).not.toHaveBeenCalled();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
	it("keeps the displayed updates usable when acknowledgment fails", async () => {
		const source = adapter({
			acknowledge: vi.fn(async () => {
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

	it("reports an acknowledgement failure under Strict Mode", async () => {
		const source = adapter({
			acknowledge: vi.fn(async () => {
				throw new Error("offline");
			}),
		});
		renderWithProviders(
			<StrictMode>
				<ProductUpdatesDialog adminId="admin-a" adapter={source} />
			</StrictMode>,
		);

		expect(
			await screen.findByText(/couldn't be saved as seen/),
		).toBeVisible();
	});
	it("leaves direct history visits unobstructed", () => {
		const source = adapter();
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
			{ initialEntries: ["/whats-new"] },
		);
		expect(source.getFeed).not.toHaveBeenCalled();
		expect(source.acknowledge).not.toHaveBeenCalled();
	});

	it("does not reopen a presented UUID after its revision changes, but presents a new UUID", async () => {
		let currentBundle = { ...bundle, entries: [{ ...bundle.entries[1] }] };
		const seenEntryIds = new Set<string>();
		const source = adapter({
			getFeed: vi.fn(
				async () =>
					({
						bundle: currentBundle,
						seen_entry_ids: [...seenEntryIds],
					}) as never,
			),
			acknowledge: vi.fn(async (entryIds: readonly string[]) => {
				for (const entryId of entryIds) seenEntryIds.add(entryId);
				return { seen_entry_ids: [...seenEntryIds] } as never;
			}),
		});
		const first = renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await screen.findByRole("dialog", { name: "What's New" });
		await waitFor(() => expect(seenEntryIds).toEqual(new Set(["new"])));
		first.unmount();

		currentBundle = {
			...currentBundle,
			entries: [{ ...currentBundle.entries[0], revision: 2 }],
		};
		const second = renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getFeed).toHaveBeenCalledTimes(2));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		second.unmount();

		currentBundle = {
			...currentBundle,
			entries: [
				...currentBundle.entries,
				{
					...currentBundle.entries[0],
					id: "new-capability",
					revision: 1,
				},
			],
		};
		const third = renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		expect(
			await screen.findByRole("dialog", { name: "What's New" }),
		).toBeVisible();
		third.unmount();
	});

	it("suppresses an in-flight feed batch presented by another tab for the same admin", async () => {
		let resolveFeed: ((value: never) => void) | undefined;
		const source = adapter({
			getFeed: vi.fn(
				() =>
					new Promise<never>((resolve) => {
						resolveFeed = resolve;
					}),
			),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getFeed).toHaveBeenCalled());
		act(() => {
			channels[0].onmessage?.({
				data: { adminId: "admin-a", entryIds: ["new"] },
			} as MessageEvent<unknown>);
		});
		resolveFeed?.({ bundle, seen_entry_ids: ["seen"] } as never);

		await waitFor(() => expect(source.getFeed).toHaveBeenCalledTimes(2));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(source.acknowledge).not.toHaveBeenCalled();
	});

	it("does not suppress a different admin's cross-tab presentation", async () => {
		let resolveFeed: ((value: never) => void) | undefined;
		const source = adapter({
			getFeed: vi.fn(
				() =>
					new Promise<never>((resolve) => {
						resolveFeed = resolve;
					}),
			),
		});
		renderWithProviders(
			<ProductUpdatesDialog adminId="admin-a" adapter={source} />,
		);
		await waitFor(() => expect(source.getFeed).toHaveBeenCalled());
		act(() => {
			channels[0].onmessage?.({
				data: { adminId: "admin-b", entryIds: ["new"] },
			} as MessageEvent<unknown>);
		});
		resolveFeed?.({ bundle, seen_entry_ids: ["seen"] } as never);

		expect(
			await screen.findByRole("dialog", { name: "What's New" }),
		).toBeVisible();
	});
});
