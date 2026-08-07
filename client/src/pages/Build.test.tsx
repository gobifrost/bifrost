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
	};
});

function solution(
	overrides: Partial<BuilderSolution> = {},
): BuilderSolution {
	return {
		id: "sol-1",
		slug: "expense-tracker",
		name: "Expense Tracker",
		visibility: "private",
		owner_user_id: "user-1",
		organization_id: "org-1",
		app_origin: null,
		status: "active",
		promotion_status: null,
		created_at: "2026-07-25T10:00:00Z",
		updated_at: "2026-07-25T10:00:00Z",
		...overrides,
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
	mockAuth.mockReturnValue({ isPlatformAdmin: false });
	mockUseBuilderAccess.mockReturnValue({
		aiConfigured: true,
		canBuild: true,
		hasPermission: true,
		isLoading: false,
		solutions: [],
	});
});

describe("Build home", () => {
	it("starts with an app-first prompt and an empty build history", () => {
		renderWithProviders(<Build />);

		expect(
			screen.getByRole("heading", { name: /what do you want to build/i }),
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
					viewTransition: true,
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
			await screen.findByText("Starting the builder"),
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
		const openButtons = screen.getAllByRole("button", {
			name: /open builder/i,
		});
		await user.click(openButtons[0]);

		expect(mockNavigate).toHaveBeenCalledWith(
			"/solutions/sol-2/builder",
		);
	});

	it("fails closed when the capability probe is unavailable", () => {
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: false,
			canBuild: false,
			hasPermission: false,
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
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockUseBuilderAccess.mockReturnValue({
			aiConfigured: false,
			canBuild: true,
			hasPermission: true,
			isLoading: false,
			solutions: [],
		});
		const { user } = renderWithProviders(<Build />);

		expect(
			screen.getByRole("heading", { name: /connect ai to start building/i }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /configure ai/i }));
		expect(mockNavigate).toHaveBeenCalledWith("/settings/ai", {
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
