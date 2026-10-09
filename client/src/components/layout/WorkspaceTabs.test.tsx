import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { WorkspaceTabs } from "./WorkspaceTabs";
const state = vi.hoisted(() => ({ admin: true }));
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: state.admin }),
}));
describe("WorkspaceTabs", () => {
	it("uses title-sized view links and identifies the active view as the page heading", () => {
		state.admin = true;
		renderWithProviders(<WorkspaceTabs />, {
			initialEntries: ["/dashboard"],
		});
		const dashboard = screen.getByRole("link", { name: "Dashboard" });
		expect(dashboard).toHaveAttribute("aria-current", "page");
		expect(dashboard).toContainElement(
			screen.getByRole("heading", { name: "Dashboard", level: 1 }),
		);
		expect(screen.getByRole("link", { name: "Workspace" })).toHaveAttribute(
			"href",
			"/",
		);
	});
	it("shows a plain Workspace title to ordinary users", () => {
		state.admin = false;
		renderWithProviders(<WorkspaceTabs />);
		expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
		expect(
			screen.getByRole("heading", { name: "Workspace", level: 1 }),
		).toBeInTheDocument();
	});
	it("renders the tabs it is given, whatever the caller's role", () => {
		state.admin = false;
		renderWithProviders(
			<WorkspaceTabs
				label="User views"
				tabs={[
					{ to: "/users", label: "Users" },
					{ to: "/users/identities", label: "Identities" },
				]}
			/>,
			{ initialEntries: ["/users/identities"] },
		);
		expect(
			screen.getByRole("navigation", { name: "User views" }),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Users" })).not.toHaveAttribute(
			"aria-current",
		);
		expect(
			screen.getByRole("link", { name: "Identities" }),
		).toContainElement(
			screen.getByRole("heading", { name: "Identities", level: 1 }),
		);
	});
});
