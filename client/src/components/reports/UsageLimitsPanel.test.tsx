import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";
import { UsageLimitsPanel } from "./UsageLimitsPanel";

const boundaryState = vi.hoisted(() => ({
	selectedBoundary: "organization:org-1" as string | undefined,
	selectedTarget: { kind: "organization", label: "Acme Co" } as
		| { kind: string; label?: string }
		| undefined,
	capabilities: new Set<string>([
		"metrics.read",
		"metrics.readwrite",
		"users.read",
		"solutions.read",
		"builder.read",
	]),
}));

const services = vi.hoisted(() => ({
	listUsageLimits: vi.fn(),
	getEffectiveUsageLimits: vi.fn(),
	saveUsageLimit: vi.fn(),
	deleteUsageLimit: vi.fn(),
	listBuilderSolutions: vi.fn(),
	useOrganizations: vi.fn(),
	useUsersFiltered: vi.fn(),
}));

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => ({
		selectedBoundary: boundaryState.selectedBoundary,
		selectedTarget: boundaryState.selectedTarget,
		isLoading: false,
		hasSelectedCapability: (capability: string) =>
			boundaryState.capabilities.has(capability),
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => services.useOrganizations(...args),
}));

vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: (...args: unknown[]) => services.useUsersFiltered(...args),
}));

vi.mock("@/services/builder", () => ({
	listBuilderSolutions: (...args: unknown[]) =>
		services.listBuilderSolutions(...args),
}));

vi.mock("@/services/usageLimits", () => ({
	listUsageLimits: (...args: unknown[]) => services.listUsageLimits(...args),
	getEffectiveUsageLimits: (...args: unknown[]) =>
		services.getEffectiveUsageLimits(...args),
	saveUsageLimit: (...args: unknown[]) => services.saveUsageLimit(...args),
	deleteUsageLimit: (...args: unknown[]) => services.deleteUsageLimit(...args),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

function seedServices() {
	services.useOrganizations.mockReturnValue({ data: [] });
	services.useUsersFiltered.mockReturnValue({
		data: [{ id: "user-1", name: "Alex Builder", email: "alex@example.com" }],
	});
	services.listBuilderSolutions.mockResolvedValue({
		solutions: [{ id: "solution-1", name: "Expense Tracker", slug: "expense" }],
		total: 1,
	});
	services.listUsageLimits.mockResolvedValue({
		policies: [
			{
				id: 1,
				scope: "organization",
				scope_key: "org-1",
				organization_id: "org-1",
				user_id: null,
				solution_id: null,
				per_run: { model_requests: 3 },
				aggregate: { total_tokens: 1000 },
				aggregate_period: "monthly",
				created_at: "2026-08-20T00:00:00Z",
				updated_at: "2026-08-20T00:00:00Z",
			},
		],
	});
	services.getEffectiveUsageLimits.mockResolvedValue({
		subject_scope: "organization",
		organization_id: "org-1",
		effective_per_run_scope: "organization",
		effective_per_run: { model_requests: 3 },
		aggregate: [
			{
				scope: "organization",
				aggregate_period: "monthly",
				period_start: "2026-08-01",
				usage: { total_tokens: 250 },
				ceilings: { total_tokens: 1000 },
				dimensions: [
					{
						dimension: "total_tokens",
						limit: 1000,
						current: 250,
						remaining: 750,
						percentage: 25,
					},
				],
			},
		],
	});
	services.saveUsageLimit.mockResolvedValue({ id: 2 });
	services.deleteUsageLimit.mockResolvedValue(undefined);
}

describe("UsageLimitsPanel", () => {
	beforeEach(() => {
		boundaryState.selectedBoundary = "organization:org-1";
		boundaryState.selectedTarget = { kind: "organization", label: "Acme Co" };
		boundaryState.capabilities = new Set([
			"metrics.read",
			"metrics.readwrite",
			"users.read",
			"solutions.read",
			"builder.read",
		]);
		for (const mock of Object.values(services)) mock.mockReset();
		seedServices();
	});

	it("prompts for an exact boundary when managed organizations is selected", () => {
		boundaryState.selectedBoundary = "managed_organizations";
		boundaryState.selectedTarget = { kind: "managed_organizations" };

		renderWithProviders(<UsageLimitsPanel />);

		expect(
			screen.getByText(/choose global or one exact organization/i),
		).toBeVisible();
		expect(services.listUsageLimits).not.toHaveBeenCalled();
	});

	it("renders effective winner and aggregate progress with friendly labels", async () => {
		renderWithProviders(<UsageLimitsPanel />);

		expect(await screen.findByText(/portable usage limits/i)).toBeVisible();
		expect(
			await screen.findByText(/Organization supplies the per-run ceiling/i),
		).toBeVisible();
		expect(screen.getAllByText(/Model requests/i).length).toBeGreaterThan(0);
		expect(screen.getAllByText(/Total tokens/i).length).toBeGreaterThan(0);
		expect(screen.queryByText(/total_tokens/)).not.toBeInTheDocument();
	});

	it("keeps the editor read-only without metrics.readwrite", async () => {
		boundaryState.capabilities = new Set(["metrics.read"]);

		renderWithProviders(<UsageLimitsPanel />);

		expect(await screen.findByText(/read-only/i)).toBeVisible();
		expect(screen.getByRole("button", { name: /save limit/i })).toBeDisabled();
	});

	it("saves a non-empty policy and converts duration minutes to milliseconds", async () => {
		renderWithProviders(<UsageLimitsPanel />);

		const modelRequestInputs = await screen.findAllByLabelText(/model requests/i);
		fireEvent.change(modelRequestInputs[0], { target: { value: "4" } });
		const runnerTimeInputs = screen.getAllByLabelText(/runner time/i);
		fireEvent.change(runnerTimeInputs[0], { target: { value: "2" } });
		fireEvent.click(screen.getByRole("button", { name: /save limit/i }));

		await waitFor(() => expect(services.saveUsageLimit).toHaveBeenCalled());
		expect(services.saveUsageLimit).toHaveBeenCalledWith(
			{ scope: "organization", targetId: "org-1" },
			expect.objectContaining({
				per_run: expect.objectContaining({
					model_requests: 4,
					runner_duration_ms: 120000,
				}),
			}),
			{ boundary: "organization:org-1" },
		);
	});
});
