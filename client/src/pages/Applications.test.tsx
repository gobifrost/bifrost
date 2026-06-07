/**
 * Tests for the Applications page — focused on the SolutionManagedBadge
 * affordance: managed apps show the shared admin-only badge and hide
 * Edit/Delete controls; non-managed apps keep their management controls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";

const mockUseApplications = vi.fn();
const mockUseDeleteApplication = vi.fn();
vi.mock("@/hooks/useApplications", () => ({
	useApplications: () => mockUseApplications(),
	useDeleteApplication: () => mockUseDeleteApplication(),
}));

const mockUseAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

// Heavy children that aren't relevant to the badge behaviour.
vi.mock("@/components/EntityLogo", () => ({ EntityLogo: () => null }));
vi.mock("@/components/app-builder/AppInfoDialog", () => ({
	AppInfoDialog: () => null,
}));
vi.mock("@/components/app-builder/CreateAppModal", () => ({
	CreateAppModal: () => null,
}));
vi.mock("@/components/search/SearchBox", () => ({ SearchBox: () => null }));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

function makeApp(overrides: Partial<Record<string, unknown>> = {}) {
	return {
		id: "app-1",
		name: "Live Dash",
		slug: "live-dash",
		description: "A dashboard",
		organization_id: null,
		is_published: true,
		has_unpublished_changes: false,
		is_solution_managed: false,
		solution_id: null,
		logo: null,
		...overrides,
	};
}

beforeEach(() => {
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseDeleteApplication.mockReturnValue({
		mutateAsync: vi.fn(),
		isPending: false,
	});
	mockUseApplications.mockReturnValue({
		data: { applications: [] },
		isLoading: false,
		refetch: vi.fn(),
	});
});

async function renderPage() {
	const { Applications } = await import("./Applications");
	return renderWithProviders(<Applications />);
}

describe("Applications — solution-managed badge (grid view)", () => {
	it("shows the badge and hides Edit/Code on a managed app", async () => {
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({
						id: "m",
						name: "Managed App",
						is_solution_managed: true,
						solution_id: "s1",
					}),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		const badge = screen.getByTestId("solution-managed-badge");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		// Managed apps must not expose Settings/Code edit controls.
		expect(
			screen.queryByRole("button", { name: /settings/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /code editor/i }),
		).not.toBeInTheDocument();
	});

	it("shows Edit/Code controls and no badge on a non-managed app", async () => {
		mockUseApplications.mockReturnValue({
			data: { applications: [makeApp()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(
			screen.queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /settings/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /code editor/i }),
		).toBeInTheDocument();
	});
});

describe("Applications — solution-managed badge (table view)", () => {
	async function renderTable(apps: ReturnType<typeof makeApp>[]) {
		mockUseApplications.mockReturnValue({
			data: { applications: apps },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(screen.getByLabelText(/table view/i));
		return user;
	}

	it("shows the badge and hides Delete on a managed app row", async () => {
		await renderTable([
			makeApp({
				id: "m",
				name: "Managed App",
				is_solution_managed: true,
				solution_id: "s1",
			}),
		]);
		const table = document.querySelector("table")!;
		const badge = within(table).getByTestId("solution-managed-badge");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		expect(
			within(table).queryByRole("button", { name: /delete/i }),
		).not.toBeInTheDocument();
	});

	it("shows Delete and no badge on a non-managed app row", async () => {
		await renderTable([makeApp()]);
		const table = document.querySelector("table")!;
		expect(
			within(table).queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			within(table).getByRole("button", { name: /delete/i }),
		).toBeInTheDocument();
	});
});
