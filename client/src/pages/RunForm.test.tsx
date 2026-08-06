import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const { mockFormRenderer, embedClaims } = vi.hoisted(() => ({
	mockFormRenderer: vi.fn((_props: unknown) => <div>Rendered Form</div>),
	embedClaims: {
		current: { embed: true, form_id: "form-1", grant: "public" },
	},
}));

vi.mock("@/hooks/useForms", () => ({
	useFormRuntime: () => ({
		data: {
			id: "form-1",
			name: "Customer Intake",
			description: "Tell us what you need",
			is_active: true,
		},
		isLoading: false,
		error: null,
	}),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		isPlatformAdmin: false,
		hasRole: (role: string) => role === "EmbedUser",
	}),
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
