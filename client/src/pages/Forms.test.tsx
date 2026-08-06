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
const mockPreloadRunFormPage = vi.fn(() =>
	Promise.resolve({ default: vi.fn() }),
);
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
vi.mock("@/components/forms/FormShareDialog", () => ({
	FormShareDialog: ({ formName }: { formName: string }) => (
		<div role="dialog">Share {formName}</div>
	),
}));
vi.mock("@/pages/run-form-route", () => ({
	preloadRunFormPage: () => mockPreloadRunFormPage(),
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
	mockPreloadRunFormPage.mockClear();
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseForms.mockReturnValue({
		data: [],
		isLoading: false,
		refetch: vi.fn(),
	});
	mockUseDeleteForm.mockReturnValue({
		mutateAsync: vi.fn(),
		isPending: false,
	});
	mockUseUpdateForm.mockReturnValue({
		mutateAsync: vi.fn(),
		isPending: false,
	});
});

async function renderPage() {
	const { Forms } = await import("./Forms");
	return renderWithProviders(<Forms />);
}

describe("Forms — solution-managed badge (grid view)", () => {
	it("preloads the form runner so Launch does not wait on its route chunk", async () => {
		await renderPage();
		expect(mockPreloadRunFormPage).toHaveBeenCalledOnce();
	});
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
		const { user } = await renderPage();
		const badge = screen.getByTestId("solution-managed-badge");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		await user.click(
			screen.getByRole("button", { name: "Managed Form actions" }),
		);
		expect(
			screen.queryByRole("menuitem", { name: "Edit Form" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: "Delete Form" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Share Form" }),
		).toBeInTheDocument();
	});

	it("shows Edit/Delete and no badge on a non-managed form", async () => {
		mockUseForms.mockReturnValue({
			data: [makeForm()],
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		expect(
			screen.queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Onboarding actions" }),
		);
		expect(screen.getByRole("menu")).toHaveClass("w-48");
		for (const item of screen.getAllByRole("menuitem")) {
			expect(item).toHaveClass("min-h-9", "whitespace-nowrap", "px-3");
		}
		expect(
			screen.getByRole("menuitem", { name: "Edit Form" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Delete Form" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Share Form" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Disable Form" }),
		).toBeInTheDocument();
	});

	it("opens the shared Share dialog from a form card", async () => {
		mockUseForms.mockReturnValue({
			data: [makeForm()],
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: "Onboarding actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Share Form" }));
		expect(screen.getByRole("dialog")).toHaveTextContent(
			"Share Onboarding",
		);
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
		const user = await renderTable([
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
		await user.click(
			within(table).getByRole("button", { name: "Managed Form actions" }),
		);
		expect(
			screen.queryByRole("menuitem", { name: "Edit Form" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: "Delete Form" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Share Form" }),
		).toBeInTheDocument();
	});

	it("shows Edit/Delete and no badge on a non-managed form row", async () => {
		const user = await renderTable([makeForm()]);
		const table = document.querySelector("table")!;
		expect(
			within(table).queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		await user.click(
			within(table).getByRole("button", { name: "Onboarding actions" }),
		);
		expect(screen.getByRole("menu")).toHaveClass("w-48");
		for (const item of screen.getAllByRole("menuitem")) {
			expect(item).toHaveClass("min-h-9", "whitespace-nowrap", "px-3");
		}
		expect(
			screen.getByRole("menuitem", { name: "Edit Form" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Delete Form" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Share Form" }),
		).toBeInTheDocument();
	});
});
