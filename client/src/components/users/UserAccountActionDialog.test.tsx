import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { UserAccountActionDialog } from "./UserAccountActionDialog";

describe("UserAccountActionDialog", () => {
	it.each(["disable", "delete", "reset-mfa", "sign-out"] as const)(
		"protects pending %s and keeps failures recoverable",
		async (mode) => {
			let reject!: (error: Error) => void;
			const onConfirm = vi
				.fn()
				.mockImplementationOnce(
					() =>
						new Promise<void>((_resolve, fail) => {
							reject = fail;
						}),
				)
				.mockResolvedValue(undefined);
			const onOpenChange = vi.fn();
			const { user } = renderWithProviders(
				<UserAccountActionDialog
					mode={mode}
					name="Alexandra Example"
					onConfirm={onConfirm}
					onOpenChange={onOpenChange}
				/>,
			);
			const action = {
				disable: /^disable$/i,
				delete: /^permanently delete$/i,
				"reset-mfa": /^reset mfa$/i,
				"sign-out": /^sign out everywhere$/i,
			}[mode];
			await user.click(screen.getByRole("button", { name: action }));
			expect(
				screen.getByRole("button", { name: "Cancel" }),
			).toBeDisabled();
			await user.keyboard("{Escape}");
			expect(onOpenChange).not.toHaveBeenCalled();
			reject(new Error("Synthetic failure"));
			await waitFor(() =>
				expect(screen.getByRole("alert")).toHaveFocus(),
			);
			expect(screen.getByRole("alert")).toHaveTextContent(
				"Synthetic failure",
			);
			await user.click(screen.getByRole("button", { name: action }));
			expect(onConfirm).toHaveBeenCalledTimes(2);
			expect(screen.queryByRole("alert")).not.toBeInTheDocument();
		},
	);

	it("says plainly what a reset removes and what happens next", () => {
		renderWithProviders(
			<UserAccountActionDialog
				mode="reset-mfa"
				name="Alexandra Example"
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(screen.getByRole("alertdialog")).toHaveTextContent(
			"Removes Alexandra Example’s authenticator app, recovery codes, passkeys and remembered devices, and signs them out everywhere. They’ll set up MFA again at their next sign-in.",
		);
	});

	it("says a sign-out is not a lockout", () => {
		renderWithProviders(
			<UserAccountActionDialog
				mode="sign-out"
				name="Alexandra Example"
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(screen.getByRole("alertdialog")).toHaveTextContent(
			"Signs Alexandra Example out on every device. They can sign in again right away.",
		);
	});
});
