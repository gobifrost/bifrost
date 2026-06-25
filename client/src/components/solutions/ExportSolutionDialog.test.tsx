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

it("requires a password when Full backup is selected", async () => {
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={() => {}}
		/>,
	);
	await userEvent.click(screen.getByLabelText(/full backup/i));
	expect(getPasswordInput()).toBeRequired();
});

it("explains what shareable and full backup exports include", async () => {
	render(<ExportSolutionDialog open onOpenChange={() => {}} onExport={() => {}} />);

	expect(screen.getByText(/definitions, table schemas/i)).toBeInTheDocument();
	expect(screen.getByText(/omits config values/i)).toBeInTheDocument();
	expect(
		screen.getByText(/config values, secret values, and Solution-owned files/i),
	).toBeInTheDocument();

	await userEvent.click(screen.getByLabelText(/full backup/i));
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

it("Export button is disabled when Full backup selected but password is empty", async () => {
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={() => {}}
		/>,
	);
	await userEvent.click(screen.getByLabelText(/full backup/i));
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

it("calls onExport with full + password when Full backup is selected and password entered", async () => {
	const onExport = vi.fn();
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={onExport}
		/>,
	);
	await userEvent.click(screen.getByLabelText(/full backup/i));
	await userEvent.type(getPasswordInput(), "s3cr3t");
	await userEvent.click(screen.getByRole("button", { name: /export/i }));
	expect(onExport).toHaveBeenCalledWith("full", "s3cr3t", false);
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

it("offers Include table data only in Full backup mode", async () => {
	render(<ExportSolutionDialog open onOpenChange={() => {}} onExport={() => {}} />);
	expect(screen.queryByLabelText(/include table data/i)).toBeNull();
	await userEvent.click(screen.getByLabelText(/full backup/i));
	expect(screen.getByLabelText(/include table data/i)).toBeInTheDocument();
});

it("resets mode + password back to Shareable after close and reopen", async () => {
	render(<ControlledExport />);

	// Select Full backup and type a password.
	await userEvent.click(screen.getByLabelText(/full backup/i));
	await userEvent.type(getPasswordInput(), "s3cr3t");
	expect(getPasswordInput()).toHaveValue("s3cr3t");

	// Close via Cancel — the dialog unmounts its content.
	await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
	expect(screen.queryByLabelText(/^password/i)).not.toBeInTheDocument();

	// Reopen — mode must be back to Shareable: the Shareable radio is checked,
	// the Full radio is not, and no password field is rendered (so the stale
	// "s3cr3t" value is gone).
	await userEvent.click(
		screen.getByRole("button", { name: /reopen-harness/i }),
	);
	expect(
		screen.getByRole("radio", { name: /shareable bundle/i }),
	).toBeChecked();
	expect(
		screen.getByRole("radio", { name: /full backup/i }),
	).not.toBeChecked();
	expect(screen.queryByLabelText(/^password/i)).not.toBeInTheDocument();
});
