import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, within } from "@/test-utils";
import type { ProductUpdateEntry } from "@/generated/product-updates";
import {
	CommunityFooter,
	UpdateEntry,
	UpdateGroups,
} from "./ProductUpdateContent";
vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <svg aria-hidden="true" />,
}));
const entry: ProductUpdateEntry = {
	id: "one",
	revision: 1,
	title: "Upgrade Notice",
	published_at: "2026-10-06T12:00:00Z",
	markdown: "[Open Users](/users)\n\n![Roles](assets/one/roles.png)",
	area: "Administration",
	type: "Security",
	action_required: true,
	sources: [{ pr: 10 }],
	contributors: [],
	assets: [
		{
			path: "assets/one/roles.png",
			url: "/roles.png",
			alt: "Roles",
			caption: "Role settings",
		},
	],
};
describe("shared product update content", () => {
	it("shows an ordinary update without an action-required label", () => {
		renderWithProviders(
			<UpdateEntry entry={{ ...entry, action_required: false }} />,
		);
		expect(screen.queryByText("Action Required")).not.toBeInTheDocument();
	});

	it("groups updates by their displayed date, newest first, with one date heading per group", () => {
		const earlier = {
			...entry,
			type: "New" as const,
			id: "earlier",
			title: "Earlier Update",
			published_at: "2026-10-05T12:00:00Z",
		};
		const sameDay = {
			...entry,
			type: "New" as const,
			id: "same-day",
			title: "Another Update",
		};
		renderWithProviders(
			<UpdateGroups
				entries={[earlier, { ...entry, type: "New" }, sameDay]}
			/>,
		);
		const format = (date: string) =>
			new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(
				new Date(date),
			);
		expect(
			screen
				.getAllByRole("heading", { level: 2 })
				.map((heading) => heading.textContent),
		).toEqual([format(entry.published_at), format(earlier.published_at)]);
		const group = screen.getByRole("region", {
			name: format(entry.published_at),
		});
		expect(
			within(group)
				.getAllByRole("heading", { level: 4 })
				.map((heading) => heading.textContent),
		).toEqual(["Upgrade Notice", "Another Update"]);
		expect(
			within(group).queryByRole("heading", { name: "Earlier Update" }),
		).not.toBeInTheDocument();
	});

	it("keeps features as headlines and corrections in separate bullet lists, excluding release-only notes", () => {
		const feature = {
			...entry,
			id: "feature",
			type: "New" as const,
			title: "A New Capability",
			action_required: false,
		};
		const fix = {
			...entry,
			id: "fix",
			type: "Fixed" as const,
			title: "A Repair",
			markdown: "A repair users can understand.",
			action_required: false,
		};
		const hidden = {
			...entry,
			id: "internal",
			title: "Dependencies",
			in_app: false,
		};
		renderWithProviders(
			<UpdateGroups
				entries={[feature, fix, entry, hidden]}
				changes={[
					{
						source: "pr:20",
						title: "A smaller fix",
						url: "https://github.com/gobifrost/bifrost/pull/20",
						contributors: [],
						category: "fix",
					},
				]}
			/>,
		);
		expect(
			screen.getByRole("heading", {
				name: "New Features and Functionality",
			}),
		).toBeVisible();
		expect(
			screen.getAllByRole("heading", { name: "Bug Fixes" }),
		).toHaveLength(1);
		expect(
			screen.getByText("A repair users can understand.").closest("li"),
		).not.toBeNull();
		expect(
			screen.getByRole("heading", { name: "Hardening" }),
		).toBeVisible();
		expect(screen.queryByText("Dependencies")).not.toBeInTheDocument();
	});

	it("preserves upgrade metadata, source links, and a useful image failure state without unread labels", () => {
		renderWithProviders(<UpdateEntry entry={entry} />);
		expect(
			screen.getByRole("heading", { name: "Upgrade Notice" }),
		).toBeVisible();
		expect(screen.getByText("Action Required")).toBeVisible();
		fireEvent.click(screen.getByText("Source Details"));
		expect(screen.getByRole("link", { name: "PR #10" })).toHaveAttribute(
			"href",
			"https://github.com/gobifrost/bifrost/pull/10",
		);
		expect(
			screen.getByRole("link", { name: "Open Users" }),
		).toHaveAttribute("target", "_blank");
		expect(screen.queryByText("Unread")).not.toBeInTheDocument();
		fireEvent.error(screen.getByRole("img", { name: "Roles" }));
		expect(screen.getByText("Screenshot unavailable: Roles")).toBeVisible();
	});
	it("keeps the three branded community destinations in the same order", () => {
		renderWithProviders(<CommunityFooter />);
		const links = screen.getAllByRole("link");
		expect(links.map((link) => link.textContent)).toEqual([
			"GitHub",
			"Discord",
			"Website",
		]);
		expect(links[1]).toHaveAttribute(
			"href",
			"https://discord.gg/f7TCcWX2s",
		);
	});
});
