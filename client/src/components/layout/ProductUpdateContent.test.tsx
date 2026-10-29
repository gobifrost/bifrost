import { describe, expect, it, vi } from "vitest";
import {
	fireEvent,
	renderWithProviders,
	screen,
	waitFor,
	within,
} from "@/test-utils";
import type { ProductUpdateEntry } from "@/services/productUpdates";
import {
	CommunityFooter,
	UpdateEntry,
	UpdateGroups,
} from "./ProductUpdateContent";
vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <img src="/covi-logo.svg" alt="" />,
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

	it("keeps newest features first without visible date groups", () => {
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
		expect(
			screen.queryByRole("heading", { level: 2 }),
		).not.toBeInTheDocument();
		const group = screen.getByRole("region", {
			name: "New Features and Functionality",
		});
		expect(
			within(group)
				.getAllByRole("heading", { level: 4 })
				.map((heading) => heading.textContent),
		).toEqual(["Upgrade Notice", "Another Update", "Earlier Update"]);
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
	describe("agent prompt", () => {
		const prompt = "Inspect my setup, then wait for my confirmation.";
		const prompted: ProductUpdateEntry = {
			...entry,
			type: "Improved",
			agent_prompt: prompt,
		};

		// renderWithProviders installs userEvent's clipboard, so stub after rendering.
		function stubClipboard(writeText: (text: string) => Promise<void>) {
			Object.defineProperty(navigator, "clipboard", {
				value: { writeText },
				configurable: true,
			});
		}

		it("copies the collapsed prompt verbatim and announces it", async () => {
			renderWithProviders(<UpdateEntry entry={prompted} />);
			const writeText = vi.fn().mockResolvedValue(undefined);
			stubClipboard(writeText);
			const callout = screen.getByRole("region", {
				name: "Agent Prompt",
			});
			expect(
				within(callout).getByText(
					"Copy this prompt to have your coding agent help you with this change.",
				),
			).toBeVisible();
			expect(within(callout).queryByText(prompt)).not.toBeVisible();

			fireEvent.click(
				within(callout).getByRole("button", { name: "Copy Prompt" }),
			);

			await waitFor(() =>
				expect(screen.getByRole("status")).toHaveTextContent(
					"Prompt copied to clipboard.",
				),
			);
			expect(writeText).toHaveBeenCalledWith(prompt);
			expect(
				within(callout).getByRole("button", { name: "Copied" }),
			).toBeVisible();
		});

		it("expands to show the prompt and collapses again", () => {
			renderWithProviders(<UpdateEntry entry={prompted} />);
			const toggle = screen.getByRole("button", { name: "Show Prompt" });
			expect(toggle).toHaveAttribute("aria-expanded", "false");

			fireEvent.click(toggle);

			expect(toggle).toHaveAttribute("aria-expanded", "true");
			expect(screen.getByText(prompt)).toBeVisible();
			fireEvent.click(
				screen.getByRole("button", { name: "Hide Prompt" }),
			);
			expect(screen.queryByText(prompt)).not.toBeVisible();
		});

		it("tells the admin to copy by hand when the clipboard fails", async () => {
			renderWithProviders(<UpdateEntry entry={prompted} />);
			stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));

			fireEvent.click(
				screen.getByRole("button", { name: "Copy Prompt" }),
			);

			expect(await screen.findByRole("alert")).toHaveTextContent(
				"Could not copy. Show the prompt and copy its text, or try again.",
			);
		});

		it("is left out of compact correction entries", () => {
			renderWithProviders(<UpdateEntry entry={prompted} compact />);
			expect(
				screen.queryByRole("button", { name: "Copy Prompt" }),
			).not.toBeInTheDocument();
		});
	});

	it("keeps the three branded community destinations in the same order", () => {
		renderWithProviders(<CommunityFooter />);
		const links = screen.getAllByRole("link");
		expect(links.map((link) => link.textContent)).toEqual([
			"GitHub",
			"Discord",
			"Website",
		]);
		const website = screen.getByRole("link", { name: "Website" });
		expect(website).toHaveAttribute("href", "https://gobifrost.com");
		expect(website).toHaveAttribute("target", "_blank");
		expect(within(website).getByRole("presentation")).toHaveAttribute(
			"src",
			"/logo.svg",
		);
		expect(links[1]).toHaveAttribute(
			"href",
			"https://discord.gg/x84pft2YDa",
		);
	});
});
