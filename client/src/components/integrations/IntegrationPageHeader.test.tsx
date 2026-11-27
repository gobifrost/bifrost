import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { IntegrationPageHeader } from "./IntegrationPageHeader";

function setup(canManageDefinitions = true) {
	const onTest = vi.fn();
	const onConfigureInstance = vi.fn();
	const onEdit = vi.fn();
	const view = renderWithProviders(<IntegrationPageHeader name="AWS"
		instanceSelector={<button>PBXs</button>} onTest={onTest}
		onConfigureInstance={onConfigureInstance} onEdit={onEdit}
		onGenerateSDK={vi.fn()} onExport={vi.fn()} canExport={false}
		hasSetupNotes={false} showSetupNotes={false} onToggleSetupNotes={vi.fn()}
		onAddSetupNotes={vi.fn()} canTestConnection canManageDefinitions={canManageDefinitions} />);
	return { ...view, onTest, onConfigureInstance, onEdit };
}

describe("IntegrationPageHeader", () => {
	it("keeps configuration accessible beside the selector and testing in overflow", async () => {
		const { user, onConfigureInstance, onTest } = setup();
		expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
		expect(screen.queryByRole("button", { name: "Test Connection" })).not.toBeInTheDocument();
		const configure = screen.getByRole("button", { name: "Configure Instance" });
		expect(configure.textContent).toBe("");
		await user.click(configure);
		expect(onConfigureInstance).toHaveBeenCalledOnce();
		await user.click(screen.getByRole("button", { name: "More integration actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Test Connection" }));
		expect(onTest).toHaveBeenCalledOnce();
	});
	it("opens the common integration editor from overflow", async () => {
		const { user, onEdit } = setup();
		await user.click(screen.getByRole("button", { name: "More integration actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit Integration" }));
		expect(onEdit).toHaveBeenCalledOnce();
	});
	it("keeps testing available without exposing admin settings", async () => {
		const { user, onTest } = setup(false);
		expect(screen.queryByRole("button", { name: "Configure Instance" })).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "More integration actions" }));
		expect(screen.queryByRole("menuitem", { name: "Edit Integration" })).not.toBeInTheDocument();
		await user.click(screen.getByRole("menuitem", { name: "Test Connection" }));
		expect(onTest).toHaveBeenCalledOnce();
	});
});
