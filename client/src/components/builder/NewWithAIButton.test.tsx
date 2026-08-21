/**
 * Tests for the builder entry point — the capability gate (a 403 hides the
 * button rather than raising an error) and navigation to the app-first home.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { NewWithAIButton, slugify } from "./NewWithAIButton";
import { BuilderApiError } from "@/services/builder";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useNavigate: () => mockNavigate };
});

const mockListBuilderSolutions = vi.fn();
const mockListBuilderTargets = vi.fn();
vi.mock("@/services/builder", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/builder")>(
			"@/services/builder",
		);
	return {
		...actual,
		listBuilderTargets: (...args: unknown[]) => mockListBuilderTargets(...args),
		listBuilderSolutions: (...args: unknown[]) =>
			mockListBuilderSolutions(...args),
	};
});

beforeEach(() => {
	mockNavigate.mockReset();
	mockListBuilderTargets.mockReset();
	mockListBuilderSolutions.mockReset();
});

describe("capability gating", () => {
	it("renders the button when the caller can build", async () => {
		mockListBuilderTargets.mockResolvedValue({
			ai_configured: true,
			builder_ready: true,
			builder_blockers: [],
			can_view_all: false,
			can_open_global_workspace: true,
			is_platform_admin: false,
			organizations: [
				{
					id: "org-1",
					name: "Org 1",
					can_execute: true,
					can_view: true,
				},
			],
		});
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			view: "mine",
			can_view_all: false,
			ai_configured: true,
			builder_ready: true,
			builder_blockers: [],
			is_platform_admin: false,
		});

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		expect(
			await screen.findByRole("button", { name: /new with ai/i }),
		).toBeInTheDocument();
	});

	it("hides the button on a 403 and shows no error", async () => {
		mockListBuilderTargets.mockRejectedValue(
			new BuilderApiError(403, "forbidden"),
		);

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		await waitFor(() => expect(mockListBuilderTargets).toHaveBeenCalled());
		expect(mockListBuilderSolutions).not.toHaveBeenCalled();
		await waitFor(() =>
			expect(screen.queryByTestId("builder-entry-point")).not.toBeInTheDocument(),
		);
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
	});

	it("hides the button while the capability is still loading", () => {
		mockListBuilderTargets.mockReturnValue(new Promise(() => {}));
		mockListBuilderSolutions.mockReturnValue(new Promise(() => {}));

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		expect(screen.queryByTestId("builder-entry-point")).not.toBeInTheDocument();
	});

	it("hides Build from ordinary users until AI is configured", async () => {
		mockListBuilderTargets.mockResolvedValue({
			ai_configured: false,
			builder_ready: false,
			builder_blockers: [],
			can_view_all: false,
			can_open_global_workspace: true,
			is_platform_admin: false,
			organizations: [
				{
					id: "org-1",
					name: "Org 1",
					can_execute: true,
					can_view: true,
				},
			],
		});
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			view: "mine",
			can_view_all: false,
			ai_configured: false,
			builder_ready: false,
			builder_blockers: [],
			is_platform_admin: false,
		});

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		await waitFor(() => expect(mockListBuilderSolutions).toHaveBeenCalled());
		expect(screen.queryByTestId("builder-entry-point")).not.toBeInTheDocument();
	});

	it("keeps Build visible to an admin who needs to configure AI", async () => {
		mockListBuilderTargets.mockResolvedValue({
			ai_configured: false,
			builder_ready: false,
			builder_blockers: [],
			can_view_all: true,
			can_open_global_workspace: true,
			is_platform_admin: true,
			organizations: [
				{
					id: "org-1",
					name: "Org 1",
					can_execute: true,
					can_view: true,
				},
			],
		});
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			view: "mine",
			can_view_all: true,
			ai_configured: false,
			builder_ready: false,
			builder_blockers: [],
			is_platform_admin: true,
		});

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		expect(await screen.findByTestId("builder-entry-point")).toBeInTheDocument();
	});
});

describe("navigation", () => {
	it("opens the shared app-first build home", async () => {
		mockListBuilderTargets.mockResolvedValue({
			ai_configured: true,
			builder_ready: true,
			builder_blockers: [],
			can_view_all: false,
			can_open_global_workspace: true,
			is_platform_admin: false,
			organizations: [
				{
					id: "org-1",
					name: "Org 1",
					can_execute: true,
					can_view: true,
				},
			],
		});
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			view: "mine",
			can_view_all: false,
			ai_configured: true,
			builder_ready: true,
			builder_blockers: [],
			is_platform_admin: false,
		});

		const { user } = renderWithProviders(
			<NewWithAIButton label="New with AI" />,
		);

		await user.click(await screen.findByTestId("builder-entry-point"));
		expect(mockNavigate).toHaveBeenCalledWith("/build");
	});
});

describe("slugify", () => {
	it("lowercases, hyphenates, and trims separators", () => {
		expect(slugify("Expense Tracker")).toBe("expense-tracker");
		expect(slugify("  My App!  ")).toBe("my-app");
		expect(slugify("A/B  Test")).toBe("a-b-test");
		expect(slugify("!!!")).toBe("");
	});
});
