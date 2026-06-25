import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";
import { ExportSolutionDialog } from "./ExportSolutionDialog";

/**
 * Controlled harness: lets a test drive `open` so we can exercise the
 * reset-on-close behaviour (select Full + type a password, close, reopen,
 * and confirm internal state was wiped back to Shareable).
 */
function ControlledExport({ onExport = () => {} }: { onExport?: () => void }) {
	const [open, setOpen] = useState(true);
	return (
		<>
			<button type="button" onClick={() => setOpen(true)}>
				reopen-harness
			</button>
			<ExportSolutionDialog
				open={open}
				onOpenChange={setOpen}
				onExport={onExport}
			/>
		</>
	);
}

// Helper: get the password input by its label association (htmlFor="export-password").
// type="password" inputs are not role="textbox", so we use getByLabelText which
// resolves <label for="export-password"> → <input id="export-password">.
function getPasswordInput() {
	return screen.getByLabelText(/^password/i);
}

it("requires a password when Backup is selected", async () => {
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={() => {}}
		/>,
	);
	await userEvent.click(screen.getByLabelText(/^backup/i));
	expect(getPasswordInput()).toBeRequired();
});

it("explains what package and backup exports include", async () => {
	render(<ExportSolutionDialog open onOpenChange={() => {}} onExport={() => {}} />);

	expect(screen.getByText(/definitions, table schemas/i)).toBeInTheDocument();
	expect(screen.getByText(/package/i)).toBeInTheDocument();
	expect(screen.getByText(/omits runtime values/i)).toBeInTheDocument();
	expect(
		screen.getByText(/choose which runtime state to include/i),
	).toBeInTheDocument();

	await userEvent.click(screen.getByLabelText(/^backup/i));
	expect(screen.getByText(/table schemas are already included/i)).toBeInTheDocument();
});

it("calls onExport with shareable + no password by default", async () => {
	const onExport = vi.fn();
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={onExport}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: /export/i }));
	expect(onExport).toHaveBeenCalledWith("shareable", undefined, undefined);
});

it("Export button is disabled when Backup is selected but password is empty", async () => {
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={() => {}}
		/>,
	);
	await userEvent.click(screen.getByLabelText(/^backup/i));
	expect(screen.getByRole("button", { name: /export/i })).toBeDisabled();
});

it("Export button is disabled and shows a spinner label when isPending", async () => {
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={() => {}}
			isPending
		/>,
	);
	const btn = screen.getByRole("button", { name: /exporting/i });
	expect(btn).toBeDisabled();
});

it("calls onExport with backup defaults and password when Backup is selected", async () => {
	const onExport = vi.fn();
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={onExport}
		/>,
	);
	await userEvent.click(screen.getByLabelText(/^backup/i));
	await userEvent.type(getPasswordInput(), "s3cr3t");
	await userEvent.click(screen.getByRole("button", { name: /export/i }));
	expect(onExport).toHaveBeenCalledWith("full", "s3cr3t", {
		includeValues: true,
		includeFiles: true,
		includeData: false,
	});
});

it("calls onOpenChange(false) when Cancel is clicked", async () => {
	const onOpenChange = vi.fn();
	render(
		<ExportSolutionDialog
			open
			onOpenChange={onOpenChange}
			onExport={() => {}}
		/>,
	);
	await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
	expect(onOpenChange).toHaveBeenCalledWith(false);
});

it("offers backup content options only in Backup mode", async () => {
	render(<ExportSolutionDialog open onOpenChange={() => {}} onExport={() => {}} />);
	expect(screen.queryByLabelText(/configuration values and secrets/i)).toBeNull();
	expect(screen.queryByLabelText(/solution-owned files/i)).toBeNull();
	expect(screen.queryByLabelText(/include table data/i)).toBeNull();

	await userEvent.click(screen.getByLabelText(/^backup/i));
	expect(screen.getByLabelText(/configuration values and secrets/i)).toBeChecked();
	expect(screen.getByLabelText(/solution-owned files/i)).toBeChecked();
	expect(screen.getByLabelText(/include table data/i)).toBeInTheDocument();
});

it("sends the selected backup content options", async () => {
	const onExport = vi.fn();
	render(<ExportSolutionDialog open onOpenChange={() => {}} onExport={onExport} />);

	await userEvent.click(screen.getByLabelText(/^backup/i));
	await userEvent.click(screen.getByLabelText(/solution-owned files/i));
	await userEvent.click(screen.getByLabelText(/include table data/i));
	await userEvent.type(getPasswordInput(), "s3cr3t");
	await userEvent.click(screen.getByRole("button", { name: /export/i }));

	expect(onExport).toHaveBeenCalledWith("full", "s3cr3t", {
		includeValues: true,
		includeFiles: false,
		includeData: true,
	});
});

it("resets mode + password back to Shareable after close and reopen", async () => {
	render(<ControlledExport />);

	// Select Backup and type a password.
	await userEvent.click(screen.getByLabelText(/^backup/i));
	await userEvent.type(getPasswordInput(), "s3cr3t");
	expect(getPasswordInput()).toHaveValue("s3cr3t");

	// Close via Cancel — the dialog unmounts its content.
	await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
	expect(screen.queryByLabelText(/^password/i)).not.toBeInTheDocument();

	// Reopen — mode must be back to Package: the Package radio is checked,
	// the Backup radio is not, and no password field is rendered (so the stale
	// "s3cr3t" value is gone).
	await userEvent.click(
		screen.getByRole("button", { name: /reopen-harness/i }),
	);
	expect(
		screen.getByRole("radio", { name: /^package/i }),
	).toBeChecked();
	expect(
		screen.getByRole("radio", { name: /^backup/i }),
	).not.toBeChecked();
	expect(screen.queryByLabelText(/^password/i)).not.toBeInTheDocument();
});
