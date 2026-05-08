import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { InviteActionsMenu } from "./InviteActionsMenu";

const baseProps = {
	userId: "u1",
	onResend: vi.fn(),
	onRegenerate: vi.fn(),
	onCopyLink: vi.fn(),
	onRevoke: vi.fn(),
};

describe("InviteActionsMenu", () => {
	it("renders nothing for active users", () => {
		const { container } = render(
			<InviteActionsMenu {...baseProps} status="active" />,
		);
		expect(container).toBeEmptyDOMElement();
	});

	it("calls onResend when Resend chosen for pending invite", async () => {
		const onResend = vi.fn();
		render(
			<InviteActionsMenu
				{...baseProps}
				onResend={onResend}
				status="pending"
			/>,
		);
		await userEvent.click(
			screen.getByRole("button", { name: /invite actions/i }),
		);
		await userEvent.click(
			screen.getByRole("menuitem", { name: /resend invite/i }),
		);
		expect(onResend).toHaveBeenCalledTimes(1);
	});

	it("does not show Revoke for never_invited (nothing to revoke)", async () => {
		render(
			<InviteActionsMenu {...baseProps} status="never_invited" />,
		);
		await userEvent.click(
			screen.getByRole("button", { name: /invite actions/i }),
		);
		// "Send invite" appears (not "Resend"), Revoke is hidden.
		expect(
			screen.getByRole("menuitem", { name: /send invite/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: /revoke/i }),
		).not.toBeInTheDocument();
	});
});
