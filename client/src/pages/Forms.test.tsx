/**
 * Tests for the Forms page — focused on the SolutionManagedBadge affordance:
 * managed forms show the shared admin-only badge and hide Edit/Delete;
 * non-managed forms keep their management controls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";

const mockUseForms = vi.fn();
const mockUseDeleteForm = vi.fn();
const mockUseUpdateForm = vi.fn();
vi.mock("@/hooks/useForms", () => ({
	useForms: () => mockUseForms(),
	useDeleteForm: () => mockUseDeleteForm(),
	useUpdateForm: () => mockUseUpdateForm(),
}));

const mockUseAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/search/SearchBox", () => ({ SearchBox: () => null }));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

function makeForm(overrides: Partial<Record<string, unknown>> = {}) {
	return {
		id: "form-1",
		name: "Onboarding",
		description: "Onboard a client",
		organization_id: null,
		is_active: true,
		is_solution_managed: false,
		solution_id: null,
		missingRequiredParams: [],
		...overrides,
	};
}

beforeEach(() => {
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseForms.mockReturnValue({
		data: [],
		isLoading: false,
		refetch: vi.fn(),
	});
	mockUseDeleteForm.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
	mockUseUpdateForm.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
});

async function renderPage() {
	const { Forms } = await import("./Forms");
	return renderWithProviders(<Forms />);
}

describe("Forms — solution-managed badge (grid view)", () => {
	it("shows the badge and hides Edit/Delete on a managed form", async () => {
		mockUseForms.mockReturnValue({
			data: [
				makeForm({
					id: "m",
					name: "Managed Form",
					is_solution_managed: true,
					solution_id: "s1",
				}),
			],
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		const badge = screen.getByTestId("solution-managed-badge");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		expect(
			screen.queryByRole("button", { name: /edit form/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /delete form/i }),
		).not.toBeInTheDocument();
	});

	it("shows Edit/Delete and no badge on a non-managed form", async () => {
		mockUseForms.mockReturnValue({
			data: [makeForm()],
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(
			screen.queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /edit form/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /delete form/i }),
		).toBeInTheDocument();
	});
});

describe("Forms — solution-managed badge (table view)", () => {
	async function renderTable(forms: ReturnType<typeof makeForm>[]) {
		mockUseForms.mockReturnValue({
			data: forms,
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(screen.getByLabelText(/table view/i));
		return user;
	}

	it("shows the badge and hides Edit/Delete on a managed form row", async () => {
		await renderTable([
			makeForm({
				id: "m",
				name: "Managed Form",
				is_solution_managed: true,
				solution_id: "s1",
			}),
		]);
		const table = document.querySelector("table")!;
		expect(
			within(table).getByTestId("solution-managed-badge"),
		).toHaveAttribute("href", "/solutions/s1");
		expect(
			within(table).queryByRole("button", { name: /edit form/i }),
		).not.toBeInTheDocument();
		expect(
			within(table).queryByRole("button", { name: /delete form/i }),
		).not.toBeInTheDocument();
	});

	it("shows Edit/Delete and no badge on a non-managed form row", async () => {
		await renderTable([makeForm()]);
		const table = document.querySelector("table")!;
		expect(
			within(table).queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			within(table).getByRole("button", { name: /edit form/i }),
		).toBeInTheDocument();
		expect(
			within(table).getByRole("button", { name: /delete form/i }),
		).toBeInTheDocument();
	});
});
