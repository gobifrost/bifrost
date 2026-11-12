/**
 * Component tests for IntegrationDefaultsDialog.
 *
 * Covers text field edits, bool field (rendered as a native <select>), the
 * required-marker, and the save submit. The dialog is controlled so we render
 * it with open=true.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";
import { IntegrationDefaultsDialog } from "./IntegrationDefaultsDialog";

function renderDialog(
	overrides: Partial<Parameters<typeof IntegrationDefaultsDialog>[0]> = {},
) {
	const onFormValuesChange = vi.fn();
	const onSave = vi.fn();
	const onOpenChange = vi.fn();
	const utils = renderWithProviders(
		<IntegrationDefaultsDialog
			open
			onOpenChange={onOpenChange}
			configSchema={[
				{ key: "tenant_id", type: "string", required: true },
				{ key: "enabled", type: "bool" },
			]}
			formValues={{}}
			onFormValuesChange={onFormValuesChange}
			onSave={onSave}
			isSaving={false}
			{...overrides}
		/>,
	);
	return { ...utils, onFormValuesChange, onSave, onOpenChange };
}

describe("IntegrationDefaultsDialog — fields", () => {
	it("renders a field per schema entry with required markers", () => {
		renderDialog();
		expect(screen.getByLabelText(/tenant_id/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/enabled/i)).toBeInTheDocument();
		// required marker on tenant_id
		expect(screen.getByText("*")).toBeInTheDocument();
	});

	it("bubbles up text edits through onFormValuesChange", () => {
		const { onFormValuesChange } = renderDialog();
		fireEvent.change(screen.getByLabelText(/tenant_id/i), {
			target: { value: "acme" },
		});
		expect(onFormValuesChange).toHaveBeenLastCalledWith({
			tenant_id: "acme",
		});
	});

	it("bool fields emit proper true/false payloads", () => {
		const { onFormValuesChange } = renderDialog();
		fireEvent.change(screen.getByLabelText(/enabled/i), {
			target: { value: "true" },
		});
		expect(onFormValuesChange).toHaveBeenLastCalledWith({ enabled: true });
	});
});

describe("IntegrationDefaultsDialog — save", () => {
	it("calls onSave when the submit button is clicked", async () => {
		const { user, onSave } = renderDialog();
		await user.click(
			screen.getByRole("button", { name: /save defaults/i }),
		);
		expect(onSave).toHaveBeenCalledTimes(1);
	});

	it("disables buttons and shows 'Saving...' while isSaving=true", () => {
		renderDialog({ isSaving: true });
		expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled();
		expect(screen.getByRole("button", { name: /cancel/i })).toBeDisabled();
	});
});

it("keeps fields disabled and prevents Escape dismissal during saving", async () => {
	const { user, onOpenChange } = renderDialog({ isSaving: true });
	expect(screen.getByLabelText(/tenant_id/i)).toBeDisabled();
	expect(screen.getByLabelText(/enabled/i)).toBeDisabled();
	await user.keyboard("{Escape}");
	expect(onOpenChange).not.toHaveBeenCalled();
});

it("keeps Not set distinct from false", () => {
	const { onFormValuesChange } = renderDialog({
		formValues: { enabled: true },
	});
	fireEvent.change(screen.getByLabelText(/enabled/i), {
		target: { value: "" },
	});
	expect(onFormValuesChange).toHaveBeenLastCalledWith({ enabled: "" });
});

it("does not submit defaults while setup instructions are collapsed", async () => {
	const { user, onSave } = renderDialog({ notes: "## Prepare credentials" });
	await user.click(screen.getByRole("button", { name: "Collapse setup instructions" }));
	expect(onSave).not.toHaveBeenCalled();
	await user.click(screen.getByRole("button", { name: "Expand setup instructions" }));
	expect(onSave).not.toHaveBeenCalled();
});
it("preserves zero and invalid integer drafts until save validation", () => {
	const { onFormValuesChange } = renderDialog({
		configSchema: [{ key: "count", type: "int" }],
	});
	fireEvent.change(screen.getByLabelText(/count/i), {
		target: { value: "0" },
	});
	expect(onFormValuesChange).toHaveBeenLastCalledWith({ count: "0" });
	fireEvent.change(screen.getByLabelText(/count/i), {
		target: { value: "12abc" },
	});
	expect(onFormValuesChange).toHaveBeenLastCalledWith({ count: "12abc" });
});

it("renders stored JSON as multiline text and preserves unfinished JSON edits", () => {
	const { onFormValuesChange } = renderDialog({
		configSchema: [{ key: "options", type: "json" }],
		formValues: { options: { enabled: true } },
	});
	const input = screen.getByRole("textbox", { name: /options/i });
	expect(input).toHaveValue(JSON.stringify({ enabled: true }, null, 2));
	fireEvent.change(input, { target: { value: '{"enabled":' } });
	expect(onFormValuesChange).toHaveBeenLastCalledWith({
		options: '{"enabled":',
	});
});

it("offers packaged static choices when setting a default", async () => {
	const { user, onFormValuesChange } = renderDialog({
		configSchema: [
			{ key: "region", type: "string", options: ["us", "eu"] },
		],
		formValues: { region: "us" },
	});
	await user.click(screen.getByRole("combobox", { name: /region/ }));
	await user.click(screen.getByRole("option", { name: "eu" }));
	expect(onFormValuesChange).toHaveBeenLastCalledWith({ region: "eu" });
});
