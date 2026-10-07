import { expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { HelpMenu } from "./HelpMenu";
vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <svg aria-hidden="true" />,
}));
it("groups documentation, internal release notes, community destinations and the installed version", async () => {
	const { user } = renderWithProviders(<HelpMenu />);
	await user.click(screen.getByRole("button", { name: "Help" }));
	expect(
		screen.getByRole("menuitem", { name: "Documentation" }),
	).toHaveAttribute("href", "https://gobifrost.com/docs/");
	expect(
		screen.getByRole("menuitem", { name: "Release Notes" }),
	).toHaveAttribute("href", "/whats-new");
	expect(screen.getByRole("menuitem", { name: "Discord" })).toHaveAttribute(
		"href",
		"https://discord.gg/x84pft2YDa",
	);
	expect(
		screen.getByRole("menuitem", { name: /Copy version/ }),
	).toBeVisible();
	expect(
		screen.queryByRole("menuitem", { name: "Settings" }),
	).not.toBeInTheDocument();
});
