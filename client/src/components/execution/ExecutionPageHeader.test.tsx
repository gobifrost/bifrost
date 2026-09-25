import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ExecutionPageHeader } from "./ExecutionPageHeader";

it("keeps available execution actions distinct and respects their pending state", async () => {
	const user = userEvent.setup();
	const props = {
		name: "Invoice workflow",
		status: "Completed",
		onBack: vi.fn(),
		onCopyId: vi.fn(),
		onOpenEditor: vi.fn(),
		onRerun: vi.fn(),
		openingEditor: false,
		rerunning: false,
	};
	const { rerender } = render(<ExecutionPageHeader {...props} />);
	for (const [name, callback] of [
		["Back to history", props.onBack],
		["Copy execution ID", props.onCopyId],
		["Editor", props.onOpenEditor],
		["Rerun", props.onRerun],
	] as const) {
		await user.click(screen.getByRole("button", { name }));
		expect(callback).toHaveBeenCalledOnce();
	}
	rerender(<ExecutionPageHeader {...props} openingEditor rerunning />);
	expect(screen.getByRole("button", { name: "Editor" })).toBeDisabled();
	expect(screen.getByRole("button", { name: "Rerun" })).toBeDisabled();
	rerender(
		<ExecutionPageHeader
			{...props}
			onOpenEditor={undefined}
			onRerun={undefined}
		/>,
	);
	expect(
		screen.queryByRole("group", { name: "Execution actions" }),
	).not.toBeInTheDocument();
	rerender(<ExecutionPageHeader {...props} backLabel="Usage" />);
	expect(screen.getByRole("button", { name: "Back to usage" })).toBeVisible();
});
