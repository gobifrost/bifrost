import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.fn();
const updateSettings = vi.fn();

vi.mock("@/services/memory", () => ({
	getPlatformMemorySettings: () => getSettings(),
	updatePlatformMemorySettings: (enabled: boolean) => updateSettings(enabled),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { MemorySettings } from "./MemorySettings";

describe("MemorySettings", () => {
	beforeEach(() => {
		getSettings.mockReset().mockResolvedValue({ enabled: false });
		updateSettings.mockReset().mockResolvedValue({ enabled: true });
	});

	it("lets a platform administrator enable memory", async () => {
		const user = userEvent.setup();
		render(<MemorySettings />);

		const toggle = await screen.findByRole("switch", {
			name: "Enable Memory",
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(
			screen.getByText("Users can disable memory in their preferences."),
		).toBeInTheDocument();
		await user.click(toggle);

		await waitFor(() => expect(updateSettings).toHaveBeenCalledWith(true));
		expect(toggle).toHaveAttribute("aria-checked", "true");
	});
});
