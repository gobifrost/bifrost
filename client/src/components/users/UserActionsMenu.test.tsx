import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UserActionsMenu } from "./UserActionsMenu";

function makeProps(
	overrides: Partial<React.ComponentProps<typeof UserActionsMenu>> = {},
) {
	return {
		status: "active",
		isActive: true,
		isSelf: false,
		canSupport: true,
		canDelete: true,
		canEditProfile: true,
		onResend: vi.fn(),
		onRegenerate: vi.fn(),
		onCopyLink: vi.fn(),
		onRevoke: vi.fn(),
		onResetMfa: vi.fn(),
		onSignOut: vi.fn(),
		onToggleActive: vi.fn(),
		onDelete: vi.fn(),
		onEditProfile: vi.fn(),
		...overrides,
	};
}

describe("UserActionsMenu", () => {
	it("active user shows Disable + Delete but no invite actions", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps()} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByRole("menu")).toHaveClass(
			"w-max",
			"whitespace-nowrap",
		);
		expect(
			screen.getByRole("menuitem", { name: "Disable" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Delete" }),
		).toBeInTheDocument();
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
		expect(
			screen.getByText(/generate registration link/i),
		).toBeInTheDocument();
		expect(screen.getByText(/copy registration link/i)).toBeInTheDocument();
		expect(screen.getByText(/revoke invite/i)).toBeInTheDocument();
	});

	it("disabled user offers Enable instead of Disable", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ isActive: false })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(
			screen.getByRole("menuitem", { name: "Enable" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: "Disable" }),
		).not.toBeInTheDocument();
	});

	it("isSelf marks Disable and Delete as disabled", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ isSelf: true })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(
			screen.getByText(/^disable$/i).closest('[role="menuitem"]'),
		).toHaveAttribute("data-disabled");
		expect(
			screen.getByText(/^delete$/i).closest('[role="menuitem"]'),
		).toHaveAttribute("data-disabled");
		for (const name of [/^reset mfa$/i, /sign out of all devices/i]) {
			expect(
				screen.getByText(name).closest('[role="menuitem"]'),
			).toHaveAttribute("data-disabled");
		}
	});

	it("offers Reset MFA and Sign out of all devices to support callers", async () => {
		const user = userEvent.setup();
		const onResetMfa = vi.fn();
		const onSignOut = vi.fn();
		render(<UserActionsMenu {...makeProps({ onResetMfa, onSignOut })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(screen.getByRole("menuitem", { name: "Reset MFA" }));
		expect(onResetMfa).toHaveBeenCalledTimes(1);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(
			screen.getByRole("menuitem", { name: "Sign out of all devices" }),
		);
		expect(onSignOut).toHaveBeenCalledTimes(1);
	});

	it("hides Reset MFA and Sign out when the caller can only delete", async () => {
		const user = userEvent.setup();
		render(<UserActionsMenu {...makeProps({ canSupport: false })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(
			screen.queryByRole("menuitem", { name: "Reset MFA" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: "Sign out of all devices" }),
		).not.toBeInTheDocument();
	});

	it("fires onResend / onRegenerate / onCopyLink / onRevoke from menu items", async () => {
		const user = userEvent.setup();
		const handlers = {
			onResend: vi.fn(),
			onRegenerate: vi.fn(),
			onCopyLink: vi.fn(),
			onRevoke: vi.fn(),
		};
		render(
			<UserActionsMenu
				{...makeProps({ status: "pending", ...handlers })}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(screen.getByText(/resend invite/i));
		expect(handlers.onResend).toHaveBeenCalledTimes(1);
	});

	it("hides what the caller's roles don't grant", async () => {
		const user = userEvent.setup();
		render(
			<UserActionsMenu
				{...makeProps({ status: "pending", canDelete: false })}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(screen.getByText(/resend invite/i)).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Disable" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: "Delete" }),
		).not.toBeInTheDocument();
	});

	it("opens the profile editor from Edit profile", async () => {
		const user = userEvent.setup();
		const onEditProfile = vi.fn();
		render(<UserActionsMenu {...makeProps({ onEditProfile })} />);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(
			screen.getByRole("menuitem", { name: "Edit profile" }),
		);
		expect(onEditProfile).toHaveBeenCalledOnce();
	});

	it("hides Edit profile when the caller can change no profile field", async () => {
		const user = userEvent.setup();
		render(
			<UserActionsMenu
				{...makeProps({ canSupport: false, canEditProfile: false })}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(
			screen.queryByRole("menuitem", { name: "Edit profile" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Delete" }),
		).toBeInTheDocument();
	});

	it("lets people edit their own profile even without user permissions", async () => {
		const user = userEvent.setup();
		render(
			<UserActionsMenu
				{...makeProps({
					isSelf: true,
					canSupport: false,
					canDelete: false,
				})}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(
			screen.getByRole("menuitem", { name: "Edit profile" }),
		).not.toHaveAttribute("data-disabled");
		expect(
			screen.queryByRole("menuitem", { name: "Delete" }),
		).not.toBeInTheDocument();
	});

	it("renders nothing when the caller can change nothing", () => {
		render(
			<UserActionsMenu
				{...makeProps({ canSupport: false, canDelete: false })}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /user actions/i }),
		).not.toBeInTheDocument();
	});

	it("shows a protected account's actions disabled, with why", async () => {
		const user = userEvent.setup();
		render(
			<UserActionsMenu
				{...makeProps({ status: "pending", isProtected: true })}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /user actions/i }));
		expect(
			screen.getByText(/only a platform admin can change it/i),
		).toBeInTheDocument();
		for (const name of [
			/edit profile/i,
			/resend invite/i,
			/^reset mfa$/i,
			/sign out of all devices/i,
			/^disable$/i,
			/^delete$/i,
		]) {
			expect(
				screen.getByText(name).closest('[role="menuitem"]'),
			).toHaveAttribute("data-disabled");
		}
	});
});
