/**
 * Tests for BulkActionBar.
 *
 * Covers: hidden when count=0, count rendering, active-mix button rules,
 * permission gating, each button fires its callback.
 */

import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { renderWithProviders, screen } from "@/test-utils";
import { BulkActionBar } from "./BulkActionBar";

const defaults = {
	count: 2,
	activeMix: "all_active" as const,
	canMoveOrg: true,
	canReplaceRoles: true,
	canSetActive: true,
	onClear: vi.fn(),
	onMoveOrg: vi.fn(),
	onReplaceRoles: vi.fn(),
	onDisable: vi.fn(),
	onEnable: vi.fn(),
};

describe("BulkActionBar", () => {
	it("renders nothing when count is 0", () => {
		const { container } = renderWithProviders(
			<BulkActionBar {...defaults} count={0} />,
		);
		expect(container).toBeEmptyDOMElement();
	});

	it("shows the selection count", () => {
		renderWithProviders(<BulkActionBar {...defaults} count={5} />);
		expect(screen.getByText("5 selected")).toBeInTheDocument();
	});

	it("shows Disable only when every selected user is active", () => {
		renderWithProviders(
			<BulkActionBar {...defaults} activeMix="all_active" />,
		);
		expect(
			screen.getByRole("button", { name: /disable/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /^enable$/i }),
		).not.toBeInTheDocument();
	});

	it("shows Enable only when every selected user is inactive", () => {
		renderWithProviders(
			<BulkActionBar {...defaults} activeMix="all_inactive" />,
		);
		expect(
			screen.getByRole("button", { name: /^enable$/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /^disable$/i }),
		).not.toBeInTheDocument();
	});

	it("shows both Disable and Enable when the selection is mixed", () => {
		renderWithProviders(<BulkActionBar {...defaults} activeMix="mixed" />);
		expect(
			screen.getByRole("button", { name: /^disable$/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /^enable$/i }),
		).toBeInTheDocument();
	});

	it("fires each callback on click", async () => {
		const user = userEvent.setup();
		const handlers = {
			onClear: vi.fn(),
			onMoveOrg: vi.fn(),
			onReplaceRoles: vi.fn(),
			onDisable: vi.fn(),
			onEnable: vi.fn(),
		};
		renderWithProviders(
			<BulkActionBar
				count={3}
				activeMix="mixed"
				canMoveOrg
				canReplaceRoles
				canSetActive
				{...handlers}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /move to org/i }));
		await user.click(
			screen.getByRole("button", { name: /replace roles/i }),
		);
		await user.click(screen.getByRole("button", { name: /^disable$/i }));
		await user.click(screen.getByRole("button", { name: /^enable$/i }));
		await user.click(
			screen.getByRole("button", { name: /clear selection/i }),
		);

		expect(handlers.onMoveOrg).toHaveBeenCalledOnce();
		expect(handlers.onReplaceRoles).toHaveBeenCalledOnce();
		expect(handlers.onDisable).toHaveBeenCalledOnce();
		expect(handlers.onEnable).toHaveBeenCalledOnce();
		expect(handlers.onClear).toHaveBeenCalledOnce();
	});

	it("offers only the operations the caller may perform", () => {
		renderWithProviders(
			<BulkActionBar
				{...defaults}
				activeMix="mixed"
				canMoveOrg={false}
				canReplaceRoles={false}
			/>,
		);
		expect(
			screen.queryByRole("button", { name: /move to org/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /replace roles/i }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /^disable$/i }),
		).toBeInTheDocument();
	});
});
