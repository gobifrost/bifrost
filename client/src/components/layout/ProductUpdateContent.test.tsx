import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import type { ProductUpdateEntry } from "@/generated/product-updates";
import { CommunityFooter, UpdateEntry } from "./ProductUpdateContent";
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
	it("preserves upgrade metadata, source links, and a useful image failure state without unread labels", () => {
		renderWithProviders(<UpdateEntry entry={entry} />);
		expect(
			screen.getByRole("heading", { name: "Upgrade Notice" }),
		).toBeVisible();
		expect(screen.getByText("Security")).toBeVisible();
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
