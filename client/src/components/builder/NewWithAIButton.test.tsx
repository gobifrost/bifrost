/**
 * Tests for the builder entry point — the capability gate (a 403 hides the
 * button rather than raising an error) and the create-and-navigate flow.
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
const mockCreateBuilderSolution = vi.fn();
vi.mock("@/services/builder", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/builder")>(
			"@/services/builder",
		);
	return {
		...actual,
		listBuilderSolutions: (...args: unknown[]) =>
			mockListBuilderSolutions(...args),
		createBuilderSolution: (...args: unknown[]) =>
			mockCreateBuilderSolution(...args),
	};
});

beforeEach(() => {
	mockNavigate.mockReset();
	mockListBuilderSolutions.mockReset();
	mockCreateBuilderSolution.mockReset();
});

describe("capability gating", () => {
	it("renders the button when the caller can build", async () => {
		mockListBuilderSolutions.mockResolvedValue([]);

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
});

describe("create flow", () => {
	beforeEach(() => {
		mockListBuilderSolutions.mockResolvedValue([]);
	});

	it("derives a slug from the name and navigates to the new builder", async () => {
		mockCreateBuilderSolution.mockResolvedValue({ id: "sol-9" });

		const { user } = renderWithProviders(
			<NewWithAIButton label="New with AI" />,
		);

		await user.click(await screen.findByTestId("builder-entry-point"));
		await user.type(screen.getByLabelText(/name/i), "Expense Tracker");

		expect(screen.getByText("expense-tracker")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() =>
			expect(mockCreateBuilderSolution).toHaveBeenCalledWith({
				slug: "expense-tracker",
				name: "Expense Tracker",
			}),
		);
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/solutions/sol-9/builder"),
		);
	});

	it("keeps Create disabled until a name yields a slug", async () => {
		const { user } = renderWithProviders(
			<NewWithAIButton label="New with AI" />,
		);

		await user.click(await screen.findByTestId("builder-entry-point"));

		expect(screen.getByRole("button", { name: /^create$/i })).toBeDisabled();

		await user.type(screen.getByLabelText(/name/i), "Notes");

		expect(screen.getByRole("button", { name: /^create$/i })).toBeEnabled();
	});

	it("surfaces a create failure without navigating", async () => {
		mockCreateBuilderSolution.mockRejectedValue(
			new BuilderApiError(409, "Slug already in use"),
		);

		const { user } = renderWithProviders(
			<NewWithAIButton label="New with AI" />,
		);

		await user.click(await screen.findByTestId("builder-entry-point"));
		await user.type(screen.getByLabelText(/name/i), "Notes");
		await user.click(screen.getByRole("button", { name: /^create$/i }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Slug already in use",
		);
		expect(mockNavigate).not.toHaveBeenCalled();
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
