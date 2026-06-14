import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ExportSolutionDialog } from "./ExportSolutionDialog";

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
	expect(onExport).toHaveBeenCalledWith("shareable", undefined);
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
	expect(onExport).toHaveBeenCalledWith("full", "s3cr3t");
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
