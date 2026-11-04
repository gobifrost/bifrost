import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const { mockFormRenderer, embedClaims, mockUseFormRuntime, mockUseAuth } =
	vi.hoisted(() => ({
		mockFormRenderer: vi.fn((_props: unknown) => <div>Rendered Form</div>),
		embedClaims: {
			current: { embed: true, form_id: "form-1", grant: "public" },
		},
		mockUseFormRuntime: vi.fn(),
		mockUseAuth: vi.fn(),
	}));

vi.mock("@/hooks/useForms", () => ({
	useFormRuntime: (...args: unknown[]) => mockUseFormRuntime(...args),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/lib/auth-token", () => ({
	getEmbedTokenClaims: () => embedClaims.current,
}));

vi.mock("@/components/forms/FormRenderer", () => ({
	FormRenderer: (props: unknown) => mockFormRenderer(props),
}));

import { RunForm } from "./RunForm";

beforeEach(() => {
	embedClaims.current = {
		embed: true,
		form_id: "form-1",
		grant: "public",
	};
	mockUseFormRuntime.mockReset();
	mockUseFormRuntime.mockReturnValue({
		data: {
			id: "form-1",
			name: "Customer Intake",
			description: "Tell us what you need",
			is_active: true,
		},
		isLoading: false,
		error: null,
	});
	mockUseAuth.mockReturnValue({
		isPlatformAdmin: false,
		hasRole: (role: string) => role === "EmbedUser",
	});
	mockFormRenderer.mockClear();
});

afterEach(() => {
	document.documentElement.classList.remove("embed-transparent");
});

describe("RunForm embedded presentation", () => {
	it("shows the form header by default", () => {
		renderWithProviders(<RunForm />, {
			initialEntries: ["/embedded/forms/public/key"],
		});
		expect(
			screen.getByRole("heading", { name: "Customer Intake" }),
		).toBeInTheDocument();
		expect(screen.getByText("Rendered Form")).toBeInTheDocument();
	});

	it("can hide the header and make the iframe canvas transparent", () => {
		const { unmount } = renderWithProviders(<RunForm />, {
			initialEntries: [
				"/embedded/forms/public/key?header=false&background=transparent",
			],
		});
		expect(
			screen.queryByRole("heading", { name: "Customer Intake" }),
		).not.toBeInTheDocument();
		expect(document.documentElement).toHaveClass("embed-transparent");
		unmount();
		expect(document.documentElement).not.toHaveClass("embed-transparent");
	});

	it("lets HMAC submissions navigate to their scoped execution result", () => {
		embedClaims.current = {
			embed: true,
			form_id: "form-1",
			grant: "hmac",
		};
		renderWithProviders(<RunForm />, {
			initialEntries: ["/embedded/forms/hmac/form-1"],
		});

		expect(mockFormRenderer).toHaveBeenCalledWith(
			expect.objectContaining({
				preventNavigation: false,
				allowScheduling: false,
			}),
		);
	});
});

describe("RunForm non-embedded controls", () => {
	it("uses 44px controls in the normal runtime header", () => {
		mockUseAuth.mockReturnValue({
			isPlatformAdmin: false,
			hasRole: () => false,
		});

		renderWithProviders(<RunForm />, {
			initialEntries: ["/forms/form-1"],
		});

		expect(
			screen.getByRole("button", { name: "Back to Forms" }),
		).toHaveAttribute("data-size", "icon-lg");
	});

	it("uses 44px controls in the error and inactive states", () => {
		mockUseAuth.mockReturnValue({
			isPlatformAdmin: false,
			hasRole: () => false,
		});
		mockUseFormRuntime.mockReturnValue({
			data: null,
			isLoading: false,
			error: new Error("boom"),
		});

		const { unmount } = renderWithProviders(<RunForm />, {
			initialEntries: ["/forms/form-1"],
		});

		expect(
			screen.getByRole("button", { name: "Back to Forms" }),
		).toHaveAttribute("data-size", "lg");

		unmount();
		mockUseFormRuntime.mockReturnValue({
			data: {
				id: "form-1",
				name: "Customer Intake",
				description: "Tell us what you need",
				is_active: false,
			},
			isLoading: false,
			error: null,
		});

		renderWithProviders(<RunForm />, {
			initialEntries: ["/forms/form-1"],
		});

		expect(
			screen.getByRole("button", { name: "Back to Forms" }),
		).toHaveAttribute("data-size", "lg");
	});
});

it("retries an embedded load failure without exposing app navigation", async () => {
	const refetch = vi.fn();
	mockUseFormRuntime.mockReturnValue({
		data: null,
		isLoading: false,
		error: new Error("Unavailable"),
		isFetching: false,
		refetch,
	});
	const { user } = renderWithProviders(<RunForm />);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Could not load this form. Try again.",
	);
	expect(
		screen.queryByRole("button", { name: "Back to Forms" }),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry form" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("keeps the runtime mounted while a cached form refresh fails and retries", async () => {
	const refetch = vi.fn();
	const result = {
		...mockUseFormRuntime(),
		error: new Error("Refresh failed"),
		isFetching: false,
		refetch,
	};
	mockUseFormRuntime.mockReturnValue(result);
	const { user, rerender } = renderWithProviders(<RunForm />);
	expect(screen.getByText("Rendered Form")).toBeVisible();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Your entries are preserved.",
	);
	await user.click(screen.getByRole("button", { name: "Retry form" }));
	expect(refetch).toHaveBeenCalledOnce();
	mockUseFormRuntime.mockReturnValue({ ...result, isFetching: true });
	rerender(<RunForm />);
	expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
	expect(screen.getByText("Rendered Form")).toBeVisible();
});

vi.mock("@/components/execution/ExecutionOrganizationSelect", () => ({
	ExecutionOrganizationSelect: ({
		onChange,
		disabled,
	}: {
		onChange: (id: string) => void;
		disabled?: boolean;
	}) => (
		<button
			type="button"
			disabled={disabled}
			onClick={() => onChange("contoso")}
		>
			Run in Contoso
		</button>
	),
}));

it("offers organization selection only outside embed sessions and passes the target to the renderer", async () => {
	const { user, rerender } = renderWithProviders(<RunForm />);
	expect(
		screen.queryByRole("button", { name: "Run in Contoso" }),
	).not.toBeInTheDocument();
	mockUseAuth.mockReturnValue({
		isPlatformAdmin: true,
		hasRole: () => false,
	});
	rerender(<RunForm />);
	await user.click(screen.getByRole("button", { name: "Run in Contoso" }));
	expect(mockFormRenderer).toHaveBeenLastCalledWith(
		expect.objectContaining({ orgId: "contoso" }),
	);
});

it("lets a signed-in user select an organization before loading its form runtime", async () => {
	mockUseAuth.mockReturnValue({
		isPlatformAdmin: false,
		hasRole: () => false,
	});
	mockUseFormRuntime.mockImplementation((_id?: string, orgId?: string) =>
		orgId === "contoso"
			? {
					data: {
						id: "form-1",
						name: "Customer Intake",
						is_active: true,
						organization_id: null,
					},
					isLoading: false,
				}
			: {
					data: undefined,
					isLoading: false,
					error: new Error(
						"Access denied in the default organization",
					),
				},
	);
	const { user } = renderWithProviders(<RunForm />);
	expect(screen.getByRole("alert")).toHaveTextContent("Form unavailable");
	await user.click(screen.getByRole("button", { name: "Run in Contoso" }));
	expect(screen.getByText("Rendered Form")).toBeVisible();
	expect(mockUseFormRuntime).toHaveBeenLastCalledWith("form-1", "contoso");
	expect(mockFormRenderer).toHaveBeenLastCalledWith(
		expect.objectContaining({ orgId: "contoso" }),
	);
});
