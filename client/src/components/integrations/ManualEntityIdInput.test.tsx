import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ManualEntityIdInput } from "./ManualEntityIdInput";

describe("ManualEntityIdInput", () => {
	it("follows refreshed values without writing the obsolete value back on blur", async () => {
		const onCommit = vi.fn();
		const { rerender, user } = renderWithProviders(
			<ManualEntityIdInput orgId="org" value="old" onCommit={onCommit} />,
		);
		rerender(
			<ManualEntityIdInput
				orgId="org"
				value="updated"
				onCommit={onCommit}
			/>,
		);
		const input = screen.getByRole("textbox", {
			name: "External entity ID",
		});
		expect(input).toHaveValue("updated");
		await user.click(input);
		await user.tab();
		expect(onCommit).not.toHaveBeenCalled();
	});
	it("preserves edits through refresh, saves on blur and follows data after acknowledgment", async () => {
		const onCommit = vi.fn();
		const { rerender, user } = renderWithProviders(
			<ManualEntityIdInput orgId="org" value="old" onCommit={onCommit} />,
		);
		const input = screen.getByRole("textbox", {
			name: "External entity ID",
		});
		await user.clear(input);
		await user.type(input, "draft");
		rerender(
			<ManualEntityIdInput
				orgId="org"
				value="remote"
				onCommit={onCommit}
			/>,
		);
		expect(input).toHaveValue("draft");
		expect(onCommit).not.toHaveBeenCalled();
		await user.tab();
		expect(onCommit).toHaveBeenCalledExactlyOnceWith(
			"org",
			"draft",
			"draft",
		);
		rerender(
			<ManualEntityIdInput
				orgId="org"
				value="draft"
				onCommit={onCommit}
			/>,
		);
		rerender(
			<ManualEntityIdInput
				orgId="org"
				value="later"
				onCommit={onCommit}
			/>,
		);
		expect(input).toHaveValue("later");
	});

	it("renders read-only when mapping changes are unavailable", () => {
		renderWithProviders(
			<ManualEntityIdInput
				orgId="org"
				value="customer-1"
				onCommit={vi.fn()}
				disabled
			/>,
		);
		expect(
			screen.getByRole("textbox", { name: "External entity ID" }),
		).toBeDisabled();
	});
});
