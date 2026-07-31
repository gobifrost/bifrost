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
vi.mock("@/services/builder", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/builder")>(
			"@/services/builder",
		);
	return {
		...actual,
		listBuilderSolutions: (...args: unknown[]) =>
			mockListBuilderSolutions(...args),
	};
});

beforeEach(() => {
	mockNavigate.mockReset();
	mockListBuilderSolutions.mockReset();
});

describe("capability gating", () => {
	it("renders the button when the caller can build", async () => {
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			ai_configured: true,
			is_platform_admin: false,
		});

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		expect(
			await screen.findByRole("button", { name: /new with ai/i }),
		).toBeInTheDocument();
	});

	it("hides the button on a 403 and shows no error", async () => {
		mockListBuilderSolutions.mockRejectedValue(
			new BuilderApiError(403, "forbidden"),
		);

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		await waitFor(() => expect(mockListBuilderSolutions).toHaveBeenCalled());
		await waitFor(() =>
			expect(screen.queryByTestId("builder-entry-point")).not.toBeInTheDocument(),
		);
		expect(screen.queryByRole("alert")).not.toBeInTheDocument();
	});

	it("hides the button while the capability is still loading", () => {
		mockListBuilderSolutions.mockReturnValue(new Promise(() => {}));

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		expect(screen.queryByTestId("builder-entry-point")).not.toBeInTheDocument();
	});

	it("hides Build from ordinary users until AI is configured", async () => {
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			ai_configured: false,
			is_platform_admin: false,
		});

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		await waitFor(() => expect(mockListBuilderSolutions).toHaveBeenCalled());
		expect(screen.queryByTestId("builder-entry-point")).not.toBeInTheDocument();
	});

	it("keeps Build visible to an admin who needs to configure AI", async () => {
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			ai_configured: false,
			is_platform_admin: true,
		});

		renderWithProviders(<NewWithAIButton label="New with AI" />);

		expect(await screen.findByTestId("builder-entry-point")).toBeInTheDocument();
	});
});

describe("navigation", () => {
	it("opens the shared app-first build home", async () => {
		mockListBuilderSolutions.mockResolvedValue({
			solutions: [],
			total: 0,
			ai_configured: true,
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
