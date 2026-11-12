import { it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { IntegrationSchemaEditor } from "./IntegrationSchemaEditor";

it("names schema controls and routes edits/add/remove to the correct field", async () => {
	const onAdd = vi.fn(),
		onUpdate = vi.fn(),
		onRemove = vi.fn();
	const { user } = renderWithProviders(
		<IntegrationSchemaEditor
			fields={[{ key: "tenant", type: "string", required: false }]}
			onAdd={onAdd}
			onUpdate={onUpdate}
			onRemove={onRemove}
		/>,
	);
	await user.click(screen.getByRole("switch", { name: "Required" }));
	expect(onUpdate).toHaveBeenCalledWith(0, { required: true });
	await user.click(screen.getByRole("button", { name: "Add Field" }));
	expect(onAdd).toHaveBeenCalledOnce();
	await user.click(screen.getByRole("button", { name: "Remove field 1" }));
	expect(onRemove).toHaveBeenCalledWith(0);
	expect(screen.getByRole("textbox", { name: "Field key" })).toHaveValue(
		"tenant",
	);
});

it("authors display labels separately from stored values", async () => {
	const onUpdate = vi.fn();
	const { user } = renderWithProviders(
		<IntegrationSchemaEditor
			fields={[
				{
					key: "region",
					type: "string",
					required: false,
					options: ["eu"],
					option_labels: { eu: "Europe" },
				},
			]}
			onAdd={vi.fn()}
			onRemove={vi.fn()}
			onUpdate={onUpdate}
		/>,
	);
	await user.type(
		screen.getByRole("textbox", { name: "Option 1 label" }),
		"!",
	);
	expect(onUpdate).toHaveBeenLastCalledWith(0, {
		options: ["eu"],
		option_labels: { eu: "Europe!" },
	});
});
