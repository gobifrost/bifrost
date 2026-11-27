import { expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
} from "@/components/ui/dropdown-menu";
import { HelpMenu } from "./HelpMenu";
vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <img src="/covi-logo.svg" alt="" />,
}));
it("groups documentation, release notes and community destinations inside the account menu", async () => {
	const { user } = renderWithProviders(
		<DropdownMenu>
			<DropdownMenuTrigger>Account</DropdownMenuTrigger>
			<DropdownMenuContent>
				<HelpMenu />
			</DropdownMenuContent>
		</DropdownMenu>,
	);
	await user.click(screen.getByRole("button", { name: "Account" }));
	await user.click(screen.getByRole("menuitem", { name: "Help" }));
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
	const youtube = screen.getByRole("menuitem", { name: "YouTube" });
	expect(youtube).toHaveAttribute(
		"href",
		"https://www.youtube.com/@gobifrost",
	);
	expect(youtube).toHaveAttribute("target", "_blank");
	expect(youtube).toHaveAttribute("rel", "noreferrer");
	const website = screen.getByRole("menuitem", { name: "Website" });
	expect(website).toHaveAttribute("href", "https://gobifrost.com");
	expect(website).toHaveAttribute("target", "_blank");
	expect(within(website).getByRole("presentation")).toHaveAttribute(
		"src",
		"/logo.svg",
	);
	expect(
		screen.queryByRole("menuitem", { name: /Copy version/ }),
	).not.toBeInTheDocument();
	expect(
		screen.queryByRole("menuitem", { name: "Settings" }),
	).not.toBeInTheDocument();
});
