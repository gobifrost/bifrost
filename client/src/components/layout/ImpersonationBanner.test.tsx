import { describe, expect, it, vi } from "vitest";

import { usePresentationStore } from "@/stores/presentationStore";
import { renderWithProviders, screen } from "@/test-utils";

const auth = vi.hoisted(() => ({
	isImpersonating: true,
	user: { name: "Avery Example", email: "avery@example.test" },
	endImpersonation: vi.fn(),
}));
const toast = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => auth,
}));
vi.mock("sonner", () => ({ toast }));

import { ImpersonationBanner } from "./ImpersonationBanner";

describe("ImpersonationBanner", () => {
	it("preserves the session warning and exit control while hiding the identity", () => {
		usePresentationStore.getState().setEnabled(true);
		try {
			renderWithProviders(<ImpersonationBanner />);
			expect(screen.getByRole("status")).toHaveTextContent("Impersonating a user");
			expect(screen.queryByText(/Avery Example|avery@example.test/)).toBeNull();
			expect(screen.getByRole("button", { name: "Exit" })).toBeEnabled();
		} finally {
			usePresentationStore.getState().setEnabled(false);
		}
	});

	it("names the impersonated person and prevents duplicate exit requests", async () => {
		let resolveExit!: () => void;
		auth.endImpersonation.mockReset().mockImplementation(
			() =>
				new Promise<void>((resolve) => {
					resolveExit = resolve;
				}),
		);
		const { user } = renderWithProviders(<ImpersonationBanner />);

		expect(screen.getByText("avery@example.test")).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Exit" }));

		expect(auth.endImpersonation).toHaveBeenCalledOnce();
		expect(screen.getByRole("button", { name: "Exit" })).toBeDisabled();
		resolveExit();
	});

	it("keeps the banner visible and explains when exit fails", async () => {
		auth.endImpersonation.mockReset().mockRejectedValue(new Error("Session could not end"));
		const { user } = renderWithProviders(<ImpersonationBanner />);

		await user.click(screen.getByRole("button", { name: "Exit" }));

		expect(screen.getByRole("status")).toBeVisible();
		expect(toast.error).toHaveBeenCalledWith("Could not end impersonation", {
			description: "Session could not end",
		});
	});
});
