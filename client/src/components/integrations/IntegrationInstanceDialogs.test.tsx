import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import type { IntegrationInstanceOption } from "./IntegrationInstanceSelector";
import {
	AddIntegrationInstanceDialog,
	ManageIntegrationInstancesDialog,
} from "./IntegrationInstanceDialogs";

const instances: IntegrationInstanceOption[] = [
	{
		id: "default-instance",
		definition_id: "definition-1",
		key: "default",
		name: "Default",
		is_default: true,
		authentication_method: "config",
		has_oauth_config: false,
	},
	{
		id: "acme-instance",
		definition_id: "definition-1",
		key: "acme-production",
		name: "Acme Production",
		is_default: false,
		authentication_method: "oauth",
		has_oauth_config: true,
	},
];

describe("AddIntegrationInstanceDialog", () => {
	it("requires a reviewed display name and stable key before adding an OAuth instance", async () => {
		const onCreate = vi.fn().mockResolvedValue(undefined);
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<AddIntegrationInstanceDialog
				open
				onOpenChange={onOpenChange}
				onCreate={onCreate}
			/>,
		);

		await user.type(screen.getByLabelText("Instance Name"), "Acme Production");
		expect(screen.getByLabelText("Instance Key")).toHaveValue(
			"acme-production",
		);
		await user.click(screen.getByRole("radio", { name: /^OAuth/ }));
		await user.click(screen.getByRole("button", { name: "Add Instance" }));

		expect(onCreate).toHaveBeenCalledWith({
			name: "Acme Production",
			key: "acme-production",
			authentication_method: "oauth",
		});
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});
});

describe("ManageIntegrationInstancesDialog", () => {
	it("renames named instances and requires confirmation before deleting one", async () => {
		const onRename = vi.fn().mockResolvedValue(undefined);
		const onDelete = vi.fn().mockResolvedValue(undefined);
		const { user } = renderWithProviders(
			<ManageIntegrationInstancesDialog
				open
				onOpenChange={vi.fn()}
				instances={instances}
				onRename={onRename}
				onDelete={onDelete}
			/>,
		);

		const name = screen.getByLabelText("Instance name: Acme Production");
		await user.clear(name);
		await user.type(name, "Acme Sandbox");
		await user.click(
			screen.getByRole("button", { name: "Save Acme Production" }),
		);
		expect(onRename).toHaveBeenCalledWith("acme-instance", "Acme Sandbox");

		await user.click(
			screen.getByRole("button", { name: "Delete Acme Production" }),
		);
		expect(screen.getByRole("alertdialog")).toHaveTextContent(
			"Delete Acme Production?",
		);
		expect(screen.getByRole("alertdialog")).toHaveTextContent(
			"credentials, defaults, and organization mappings",
		);
		await user.click(
			screen.getByRole("button", { name: "Delete Instance" }),
		);
		expect(onDelete).toHaveBeenCalledWith("acme-instance");
	});
});
