import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UserActionsMenu } from "./UserActionsMenu";

function makeProps(overrides: Partial<React.ComponentProps<typeof UserActionsMenu>> = {}) {
	return {
		status: "active",
		isActive: true,
		isSelf: false,
		onResend: vi.fn(),
		onRegenerate: vi.fn(),
		onCopyLink: vi.fn(),
		onRevoke: vi.fn(),
		onToggleActive: vi.fn(),
		onDelete: vi.fn(),
		...overrides,
	};
}

describe("UserActionsMenu", () => {
	it("active user shows Disable + Delete but no invite actions", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps()} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByText(/disable user/i)).toBeInTheDocument();
		expect(screen.getByText(/delete permanently/i)).toBeInTheDocument();
		expect(screen.queryByText(/resend invite/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/send invite/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/revoke invite/i)).not.toBeInTheDocument();
	});

	it("never_invited user shows Send invite and no Revoke", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ status: "never_invited" })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByText(/send invite/i)).toBeInTheDocument();
		expect(screen.queryByText(/revoke invite/i)).not.toBeInTheDocument();
	});

	it("pending invite user shows Resend, Regenerate, Copy, Revoke", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ status: "pending" })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByText(/resend invite/i)).toBeInTheDocument();
		expect(screen.getByText(/generate registration link/i)).toBeInTheDocument();
		expect(screen.getByText(/copy registration link/i)).toBeInTheDocument();
		expect(screen.getByText(/revoke invite/i)).toBeInTheDocument();
	});

	it("disabled user offers Enable instead of Disable", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ isActive: false })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByText(/enable user/i)).toBeInTheDocument();
		expect(screen.queryByText(/disable user/i)).not.toBeInTheDocument();
	});

	it("isSelf marks Disable and Delete as disabled", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ isSelf: true })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByText(/disable user/i).closest('[role="menuitem"]')).toHaveAttribute(
			"data-disabled",
		);
		expect(screen.getByText(/delete permanently/i).closest('[role="menuitem"]')).toHaveAttribute(
			"data-disabled",
		);
	});

	it("fires onResend / onRegenerate / onCopyLink / onRevoke from menu items", async () => {
		const user = userEvent.setup();
		const handlers = {
			onResend: vi.fn(),
			onRegenerate: vi.fn(),
			onCopyLink: vi.fn(),
			onRevoke: vi.fn(),
		};
		render(<UserActionsMenu {...makeProps({ status: "pending", ...handlers })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(screen.getByText(/resend invite/i));
		expect(handlers.onResend).toHaveBeenCalledTimes(1);
	});
});
