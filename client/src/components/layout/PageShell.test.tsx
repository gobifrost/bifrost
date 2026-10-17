import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
const auth = {
	isAuthenticated: true,
	isLoading: false,
	isPlatformAdmin: true,
	isOrgUser: false,
	hasRole: vi.fn(() => false),
};
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("react-router-dom", async (original) => ({
	...(await original<typeof import("react-router-dom")>()),
	Outlet: () => <p>Page content</p>,
}));
vi.mock("./Header", () => ({
	Header: () => <header>Workspace header</header>,
}));
vi.mock("./Sidebar", () => ({
	Sidebar: () => <nav>Workspace navigation</nav>,
}));
vi.mock("@/components/NoAccess", () => ({ NoAccess: () => <p>No access</p> }));
import { PageShell } from "./PageShell";
beforeEach(() => {
	auth.isAuthenticated = true;
	auth.isLoading = false;
	auth.isPlatformAdmin = true;
	auth.isOrgUser = false;
	auth.hasRole.mockReturnValue(false);
});
describe("PageShell access contract", () => {
	it("shows loading without exposing routed content", () => {
		auth.isLoading = true;
		renderWithProviders(<PageShell />);
		expect(screen.getByRole("status")).toHaveTextContent(
			"Loading workspace",
		);
		expect(screen.queryByText("Page content")).not.toBeInTheDocument();
	});
	it("lets an authenticated role-only user reach route authorization", () => {
		auth.isPlatformAdmin = false;
		renderWithProviders(<PageShell />);
		expect(screen.getByText("Page content")).toBeVisible();
		expect(screen.getByRole("navigation")).toBeVisible();
	});
	it("keeps embed content free of the workspace chrome", () => {
		auth.isPlatformAdmin = false;
		auth.hasRole.mockReturnValue(true);
		renderWithProviders(<PageShell padded />);
		expect(screen.getByText("Page content")).toBeVisible();
		expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
		expect(screen.queryByRole("banner")).not.toBeInTheDocument();
	});
	it("renders the shared navigation and routed content for organization users", () => {
		auth.isPlatformAdmin = false;
		auth.isOrgUser = true;
		renderWithProviders(<PageShell />);
		expect(screen.getByRole("navigation")).toBeVisible();
		expect(screen.getByRole("banner")).toBeVisible();
		expect(screen.getByText("Page content")).toBeVisible();
	});
});

it("does not show permission denial while signed out", () => {
	auth.isAuthenticated = false;
	auth.isPlatformAdmin = false;
	renderWithProviders(<PageShell />);
	expect(
		screen.getByRole("status", { name: "Opening sign in…" }),
	).toBeVisible();
	expect(screen.queryByText("No access")).not.toBeInTheDocument();
});
