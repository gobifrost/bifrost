import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { Build } from "./Build";
import type { BuilderSolution } from "@/services/builder";

const mockNavigate = vi.fn();
const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useNavigate: () => mockNavigate };
});

const mockUseBuilderAccess = vi.fn();
vi.mock("@/hooks/useBuilderAccess", () => ({
	builderSolutionsQueryKey: ["builder", "solutions"],
	useBuilderAccess: () => mockUseBuilderAccess(),
}));

const mockCreateBuilderSolution = vi.fn();
const mockCreateBuilderSession = vi.fn();
const mockListBuilderSolutions = vi.fn();
const mockGetGlobalWorkspace = vi.fn();
const mockEnsureGlobalWorkspace = vi.fn();
vi.mock("@/services/builder", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/builder")>(
			"@/services/builder",
		);
	return {
		...actual,
		createBuilderSolution: (...args: unknown[]) =>
			mockCreateBuilderSolution(...args),
		createBuilderSession: (...args: unknown[]) =>
			mockCreateBuilderSession(...args),
		listBuilderSolutions: (...args: unknown[]) =>
			mockListBuilderSolutions(...args),
		getGlobalWorkspace: (...args: unknown[]) =>
			mockGetGlobalWorkspace(...args),
		ensureGlobalWorkspace: (...args: unknown[]) =>
			mockEnsureGlobalWorkspace(...args),
	};
});

vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: () => ({ data: [], isLoading: false }),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({ onChange }: { onChange: (value: string) => void }) => (
		<button type="button" onClick={() => onChange("org-2")}>All organizations</button>
	),
}));

function solution(
	overrides: Partial<BuilderSolution> = {},
): BuilderSolution {
	return {
		id: "sol-1",
		slug: "expense-tracker",
		name: "Expense Tracker",
		visibility: "private",
		owner_user_id: "user-1",
		owner_name: "Dev User",
		owner_email: "dev@example.com",
		organization_id: "org-1",
		organization_name: "Example Customer",
		caller_access: "owner",
		collaborator_access: null,
		status: "active",
		promotion_status: "none",
		created_at: "2026-07-25T10:00:00Z",
		updated_at: "2026-07-25T10:00:00Z",
		...overrides,
		target_kind: overrides.target_kind ?? "solution",
	};
}

beforeEach(() => {
	vi.restoreAllMocks();
	vi.clearAllMocks();
	vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
		callback(0);
		return 1;
	});
	vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
	mockAuth.mockReturnValue({ isPlatformAdmin: false, user: { id: "user-1" } });
	mockListBuilderSolutions.mockResolvedValue({
		solutions: [],
		total: 0,
		view: "all",
		can_view_all: true,
		ai_configured: true,
		builder_ready: true,
		builder_blockers: [],
		is_platform_admin: true,
	});
	mockGetGlobalWorkspace.mockResolvedValue({
		exists: false,
		solution_id: null,
		has_pending_proposal: false,
		can_rollback: false,
	});
	mockEnsureGlobalWorkspace.mockResolvedValue({
		exists: true,
		solution_id: "global-workspace-1",
		has_pending_proposal: false,
		can_rollback: false,
	});
	mockUseBuilderAccess.mockReturnValue({
		aiConfigured: true,
		canBuild: true,
		hasPermission: true,
		builderReady: true,
		blockers: [],
		canViewAll: false,
		isPlatformAdmin: false,
		isLoading: false,
		solutions: [],
	});
});

describe("Build home", () => {
	it("starts with an app-first prompt and an empty build history", () => {
		renderWithProviders(<Build />);

		expect(
			screen.getByRole("heading", { name: /what should bifrost build/i }),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/app name/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/describe your app/i)).toBeInTheDocument();
		expect(screen.getByText(/no apps in progress/i)).toBeInTheDocument();
	});

	it("creates a private app workspace and carries the prompt into its first session", async () => {
		mockCreateBuilderSolution.mockResolvedValue(solution({ id: "sol-9" }));
		mockCreateBuilderSession.mockResolvedValue({ id: "session-4" });
		const { user } = renderWithProviders(<Build />);

		await user.type(screen.getByLabelText(/app name/i), "Expense Tracker");
		await user.type(
			screen.getByLabelText(/describe your app/i),
			"Track receipts and monthly totals",
		);
		await user.click(
			screen.getByRole("button", { name: /start building/i }),
		);

		await waitFor(() =>
			expect(mockCreateBuilderSolution).toHaveBeenCalledWith({
				name: "Expense Tracker",
				slug: "expense-tracker",
			}),
		);
		expect(mockCreateBuilderSession).toHaveBeenCalledWith("sol-9");
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith(
				"/solutions/sol-9/builder",
				{
					state: {
						initialPrompt: "Track receipts and monthly totals",
						initialSessionId: "session-4",
					},
				},
			),
		);
	});

	it("shows real launch progress while the builder session is starting", async () => {
		let resolveSession: (value: { id: string }) => void = () => undefined;
		mockCreateBuilderSolution.mockResolvedValue(solution({ id: "sol-10" }));
		mockCreateBuilderSession.mockReturnValue(
			new Promise((resolve) => {
				resolveSession = resolve;
			}),
		);
		const { user } = renderWithProviders(<Build />);

		await user.type(screen.getByLabelText(/app name/i), "Policy Portal");
		await user.type(
			screen.getByLabelText(/describe your app/i),
			"Build a policy portal",
		);
		await user.click(
			screen.getByRole("button", { name: /start building/i }),
		);

		expect(
			await screen.findByText("Starting the Builder Agent"),
		).toBeInTheDocument();
		expect(screen.queryByLabelText(/app name/i)).not.toBeInTheDocument();

		resolveSession({ id: "session-10" });
		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalled();
		});
	});

	it("lists the user's private builds and reopens the selected history", async () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: true,
			canBuild: true,
			hasPermission: true,
			builderReady: true,
			blockers: [],
			canViewAll: false,
			isPlatformAdmin: false,
			isLoading: false,
			solutions: [
				solution(),
				solution({
					id: "sol-2",
					name: "Service Board",
					slug: "service-board",
					updated_at: "2026-07-26T10:00:00Z",
				}),
			],
		});
		const { user } = renderWithProviders(<Build />);

		expect(screen.getByText("Expense Tracker")).toBeInTheDocument();
		expect(screen.getByText("Service Board")).toBeInTheDocument();
		const openButtons = screen.getAllByRole("button", { name: /^open$/i });
		await user.click(openButtons[0]);

		expect(mockNavigate).toHaveBeenCalledWith(
			"/solutions/sol-2/builder",
		);
	});

	it("keeps support-wide builds behind an explicit All customer work view", async () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: true,
			canBuild: true,
			hasPermission: true,
			builderReady: true,
			blockers: [],
			canViewAll: true,
			isPlatformAdmin: true,
			isLoading: false,
			solutions: [solution()],
		});
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [solution({ id: "sol-customer", name: "Customer Inventory", owner_user_id: "user-2", owner_name: "Avery Admin", caller_access: "support" })],
			total: 1,
			view: "all",
			can_view_all: true,
			ai_configured: true,
			builder_ready: true,
			builder_blockers: [],
			is_platform_admin: true,
		});
		const { user } = renderWithProviders(<Build />);

		expect(screen.queryByText("Customer Inventory")).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: /all customer work/i }));

		expect(await screen.findByText("Customer Inventory")).toBeInTheDocument();
		expect(screen.getByText("Support access")).toBeInTheDocument();
		expect(mockListBuilderSolutions).toHaveBeenCalledWith(
			expect.objectContaining({ view: "all", signal: expect.any(AbortSignal) }),
		);
	});

	it("distinguishes an unavailable support catalog from an empty one", async () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: true,
			canBuild: true,
			hasPermission: true,
			builderReady: true,
			blockers: [],
			canViewAll: true,
			isPlatformAdmin: true,
			isLoading: false,
			solutions: [solution()],
		});
		mockListBuilderSolutions.mockRejectedValue(
			new Error("Support catalog unavailable"),
		);
		const { user } = renderWithProviders(<Build />);

		await user.click(screen.getByRole("tab", { name: /all customer work/i }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not load customer work",
		);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Support catalog unavailable",
		);
		expect(screen.queryByText("No apps in progress")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /try again/i }));
		await waitFor(() => expect(mockListBuilderSolutions).toHaveBeenCalledTimes(2));
	});

	it("pages the support catalog without loading every customer build", async () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: true,
			canBuild: true,
			hasPermission: true,
			builderReady: true,
			blockers: [],
			canViewAll: true,
			isPlatformAdmin: true,
			isLoading: false,
			solutions: [],
		});
		mockListBuilderSolutions
			.mockResolvedValueOnce({
				solutions: [solution({ id: "sol-first", name: "First page app" })],
				total: 51,
				view: "all",
				can_view_all: true,
				ai_configured: true,
				builder_ready: true,
				builder_blockers: [],
				is_platform_admin: true,
			})
			.mockResolvedValueOnce({
				solutions: [solution({ id: "sol-last", name: "Last page app" })],
				total: 51,
				view: "all",
				can_view_all: true,
				ai_configured: true,
				builder_ready: true,
				builder_blockers: [],
				is_platform_admin: true,
			});
		const { user } = renderWithProviders(<Build />);

		await user.click(screen.getByRole("tab", { name: /all customer work/i }));
		expect(await screen.findByText("Showing 1–50 of 51")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Next" }));

		expect(await screen.findByText("Last page app")).toBeInTheDocument();
		expect(screen.getByText("Showing 51–51 of 51")).toBeInTheDocument();
		expect(mockListBuilderSolutions).toHaveBeenLastCalledWith(
			expect.objectContaining({ limit: 50, offset: 50 }),
		);
	});

	it("gives platform admins an explicit Global Workspace entry point", async () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: true,
			canBuild: true,
			hasPermission: true,
			builderReady: true,
			blockers: [],
			canViewAll: true,
			isPlatformAdmin: true,
			isLoading: false,
			solutions: [],
		});
		const { user } = renderWithProviders(<Build />);

		expect(
			await screen.findByRole("heading", { name: "Global Workspace" }),
		).toBeInTheDocument();
		expect(screen.getByText(/nothing changes live until/i)).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: /create global workspace/i }),
		);

		await waitFor(() => expect(mockEnsureGlobalWorkspace).toHaveBeenCalled());
		expect(mockNavigate).toHaveBeenCalledWith(
			"/solutions/global-workspace-1/builder",
		);
	});

	it("fails closed when the capability probe is unavailable", () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: false,
			canBuild: false,
			hasPermission: false,
			builderReady: false,
			blockers: [],
			canViewAll: false,
			isPlatformAdmin: false,
			isLoading: false,
			solutions: [],
		});

		renderWithProviders(<Build />);

		expect(
			screen.getByRole("heading", { name: /build is unavailable/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /start building/i }),
		).not.toBeInTheDocument();
	});

	it("routes an admin to AI settings when no provider is configured", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true, user: { id: "user-1" } });
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: false,
			canBuild: true,
			hasPermission: true,
			builderReady: false,
			blockers: [{ code: "ai_not_configured", message: "Connect AI", action: "Choose a provider and model." }],
			canViewAll: true,
			isPlatformAdmin: true,
			isLoading: false,
			solutions: [],
		});
		const { user } = renderWithProviders(<Build />);

		expect(
			screen.getByRole("heading", { name: /finish connecting builder/i }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /open builder setup/i }));
		expect(mockNavigate).toHaveBeenCalledWith("/settings/builder", {
			viewTransition: true,
		});
	});

	it("surfaces project creation failures", async () => {
		mockCreateBuilderSolution.mockRejectedValue(
			new Error("Private app limit reached"),
		);
		const { user } = renderWithProviders(<Build />);

		await user.type(screen.getByLabelText(/app name/i), "Notes");
		await user.type(screen.getByLabelText(/describe your app/i), "Build notes");
		await user.click(
			screen.getByRole("button", { name: /start building/i }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Private app limit reached",
		);
		expect(mockNavigate).not.toHaveBeenCalled();
	});
});
