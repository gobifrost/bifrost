import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.fn();
const updateSettings = vi.fn();

vi.mock("@/services/appSdkUpgradeSettings", () => ({
	getAppSdkUpgradeSettings: () => getSettings(),
	updateAppSdkUpgradeSettings: (enabled: boolean) => updateSettings(enabled),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { AppSdkUpgradeSettings } from "./AppSdkUpgradeSettings";

describe("AppSdkUpgradeSettings", () => {
	beforeEach(() => {
		getSettings.mockReset().mockResolvedValue({ enabled: false });
		updateSettings.mockReset().mockResolvedValue({ enabled: true });
	});

	it("enables automatic upgrades only after the server confirms the change", async () => {
		const user = userEvent.setup();
		render(<AppSdkUpgradeSettings />);

		const toggle = await screen.findByRole("switch", {
			name: "Enable Automatic App SDK Upgrades",
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(toggle).not.toBeChecked();
		await user.click(toggle);

		await waitFor(() => expect(updateSettings).toHaveBeenCalledWith(true));
		expect(toggle).toBeChecked();
		expect(
			screen.getByText(/Progress and failures appear in Notifications/),
		).toBeInTheDocument();
	});

	it("keeps the saved setting and offers a retry after a failed save", async () => {
		updateSettings.mockRejectedValueOnce(new Error("Offline"));
		const user = userEvent.setup();
		render(<AppSdkUpgradeSettings />);

		const toggle = await screen.findByRole("switch", {
			name: "Enable Automatic App SDK Upgrades",
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		await user.click(toggle);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not confirm that App SDK upgrade settings were saved",
		);
		expect(toggle).not.toBeChecked();
		expect(toggle).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Retry Save" }));
		await waitFor(() => expect(updateSettings).toHaveBeenCalledTimes(2));
		await waitFor(() => expect(toggle).toBeChecked());
	});

	it("keeps the setting unavailable until loading succeeds after retry", async () => {
		getSettings.mockRejectedValueOnce(new Error("Offline"));
		const user = userEvent.setup();
		render(<AppSdkUpgradeSettings />);

		await screen.findByRole("alert");
		const toggle = screen.getByRole("switch", {
			name: "Enable Automatic App SDK Upgrades",
		});
		expect(toggle).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Retry" }));
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(updateSettings).not.toHaveBeenCalled();
	});
});
