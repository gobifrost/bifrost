import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { IntegrationInstanceSelector } from "./IntegrationInstanceSelector";

const instances = [
	{
		id: "default-instance",
		definition_id: "definition-1",
		key: "default",
		name: "AWS",
		is_default: true,
		authentication_method: "config" as const,
		has_oauth_config: false,
	},
	{
		id: "acme-instance",
		definition_id: "definition-1",
		key: "acme",
		name: "Acme Production",
		is_default: false,
		authentication_method: "oauth" as const,
		has_oauth_config: true,
	},
];

describe("IntegrationInstanceSelector", () => {
	it("switches the selected instance and exposes instance management actions", async () => {
		const onSelect = vi.fn();
		const onAddInstance = vi.fn();
		const onManageInstances = vi.fn();
		const { user } = renderWithProviders(
			<IntegrationInstanceSelector
				instances={instances}
				selectedInstanceId="default-instance"
				onSelect={onSelect}
				onAddInstance={onAddInstance}
				onManageInstances={onManageInstances}
				canManageInstances
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: "Selected instance: Default" }),
		);
		await user.click(
			screen.getByRole("menuitemradio", { name: "Acme Production" }),
		);
		expect(onSelect).toHaveBeenCalledWith("acme-instance");

		await user.click(
			screen.getByRole("button", { name: "Selected instance: Default" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Add Instance" }));
		expect(onAddInstance).toHaveBeenCalledOnce();

		await user.click(
			screen.getByRole("button", { name: "Selected instance: Default" }),
		);
		await user.click(
			screen.getByRole("menuitem", { name: "Manage Instances" }),
		);
		expect(onManageInstances).toHaveBeenCalledOnce();
	});
	it("does not present Default when the requested instance is absent", () => {
		renderWithProviders(
			<IntegrationInstanceSelector instances={instances} selectedInstanceId="missing"
				onSelect={vi.fn()} onAddInstance={vi.fn()} onManageInstances={vi.fn()} canManageInstances />,
		);
		expect(screen.queryByRole("button", { name: /Selected instance:/ })).not.toBeInTheDocument();
	});

});
