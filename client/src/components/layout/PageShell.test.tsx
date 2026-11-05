import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
const auth = {
	isAuthenticated: true,
	isLoading: false,
	isPlatformAdmin: true,
	isOrgUser: false,
	hasRole: vi.fn(() => false),
	isImpersonating: false,
	user: { name: "Avery Example", email: "avery@example.test" },
	endImpersonation: vi.fn(),
};
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
const page = { suspended: false };
const neverLoads = new Promise<never>(() => {});
function RoutedPage() {
	// A lazy page or lazy child that has not loaded yet.
	if (page.suspended) throw neverLoads;
	return <p>Page content</p>;
}
vi.mock("react-router-dom", async (original) => ({
	...(await original<typeof import("react-router-dom")>()),
	Outlet: () => <RoutedPage />,
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
	page.suspended = false;
	auth.isAuthenticated = true;
	auth.isLoading = false;
	auth.isPlatformAdmin = true;
	auth.isOrgUser = false;
	auth.hasRole.mockReturnValue(false);
	auth.isImpersonating = false;
	auth.endImpersonation.mockReset();
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
	it("keeps the navigation and header while a page is still loading", () => {
		page.suspended = true;
		renderWithProviders(<PageShell />);
		expect(screen.getByRole("navigation")).toBeVisible();
		expect(screen.getByRole("banner")).toBeVisible();
		expect(screen.getByRole("status")).toHaveTextContent("Loading page");
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

	it("keeps the impersonated person's name and email visible above the workspace", () => {
		auth.isImpersonating = true;
		renderWithProviders(<PageShell />);
		expect(
			screen.getByRole("status", { name: "Impersonation session" }),
		).toHaveTextContent("Impersonating Avery Example");
		expect(screen.getByText("avery@example.test")).toBeInTheDocument();
	});

	it("places the impersonation banner above the shared sidebar and header row", () => {
		auth.isImpersonating = true;
		renderWithProviders(<PageShell />);

		const banner = screen.getByRole("status", {
			name: "Impersonation session",
		});
		const sharedChromeRow = banner.nextElementSibling;

		expect(sharedChromeRow).toContainElement(
			screen.getByRole("navigation"),
		);
		expect(sharedChromeRow).toContainElement(
			screen.getByText("Workspace header"),
		);
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
