import { renderWithProviders, screen } from "@/test-utils";
import { expect, it, vi } from "vitest";
import { AppConnectionApproval } from "./AppConnectionApproval";
it("never decides on render and sends the explicitly chosen decision", async () => {
	const decision = vi.fn();
	const { user } = renderWithProviders(
		<AppConnectionApproval
			clientName="Bifrost CLI"
			email="user@example.com"
			deviceCode="ABCD-EFGH"
			pendingDecision={null}
			onDecision={decision}
			approveLabel="Connect CLI"
		/>,
	);
	expect(decision).not.toHaveBeenCalled();
	expect(screen.getByLabelText("Device code ABCD-EFGH")).toBeVisible();
	await user.click(screen.getByRole("button", { name: "Connect CLI" }));
	expect(decision).toHaveBeenCalledWith(true);
});
it("prevents duplicate decisions while submitting", () => {
	renderWithProviders(
		<AppConnectionApproval
			clientName="Example App"
			returnAddress="https://app.example/callback"
			pendingDecision={false}
			onDecision={vi.fn()}
		/>,
	);
	expect(screen.getByRole("button", { name: "Allow" })).toBeDisabled();
	expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
});
