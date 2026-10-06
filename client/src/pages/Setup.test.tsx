import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { Setup } from "./Setup";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		needsSetup: true,
		isLoading: false,
		checkAuthStatus: vi.fn(),
		completeLoginWithToken: vi.fn(),
	}),
}));
vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Example Portal",
}));
vi.mock("@/components/branding/Logo", () => ({
	Logo: ({ alt }: { alt: string }) => <img alt={alt} />,
}));

describe("Setup account details", () => {
	it("validates email, advances with Enter and preserves editable account details", async () => {
		const { user } = renderWithProviders(<Setup />, {
			initialEntries: ["/setup"],
		});
		const email = screen.getByLabelText("Email", { exact: true });
		await user.type(
			screen.getByLabelText("Name", { exact: true }),
			"Design Review",
		);
		await user.type(email, "invalid");
		await user.click(screen.getByRole("button", { name: "Continue" }));
		expect(
			screen.queryByRole("button", { name: "Use password instead" }),
		).not.toBeInTheDocument();
		await user.clear(email);
		await user.type(email, "review@example.invalid{Enter}");
		expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
		expect(
			screen.getByRole("button", { name: "Use password instead" }),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Change account details" }),
		);
		expect(screen.getByLabelText("Name", { exact: true })).toHaveValue(
			"Design Review",
		);
		expect(screen.getByLabelText("Email", { exact: true })).toHaveValue(
			"review@example.invalid",
		);
	});
});
