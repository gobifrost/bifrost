import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.fn();
const updateSettings = vi.fn();
const cleanup = vi.fn();

vi.mock("@/services/artifactRetention", () => ({
	getArtifactRetentionSettings: () => getSettings(),
	updateArtifactRetentionSettings: (settings: unknown) =>
		updateSettings(settings),
	cleanupExpiredArtifacts: () => cleanup(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ArtifactRetentionSettings } from "./ArtifactRetentionSettings";

describe("ArtifactRetentionSettings", () => {
	beforeEach(() => {
		getSettings.mockReset().mockResolvedValue({
			enabled: false,
			retention_days: 90,
		});
		updateSettings
			.mockReset()
			.mockImplementation((settings) => Promise.resolve(settings));
		cleanup.mockReset().mockResolvedValue({
			job_id: "job-1",
			status: "queued",
			reused: false,
			notification_id: "notification-1",
		});
	});

	it("lets a platform administrator enable scheduled cleanup", async () => {
		const user = userEvent.setup();
		render(<ArtifactRetentionSettings />);

		const toggle = await screen.findByRole("switch", {
			name: "Enable Scheduled Cleanup",
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		await user.click(toggle);

		await waitFor(() =>
			expect(updateSettings).toHaveBeenCalledWith({
				enabled: true,
				retention_days: 90,
			}),
		);
	});

	it("saves retention days and runs cleanup", async () => {
		const user = userEvent.setup();
		render(<ArtifactRetentionSettings />);

		const days = await screen.findByLabelText("Retention Days");
		await waitFor(() => expect(days).toBeEnabled());
		fireEvent.change(days, { target: { value: "30" } });
		fireEvent.blur(days);
		await waitFor(() =>
			expect(updateSettings).toHaveBeenCalledWith({
				enabled: false,
				retention_days: 30,
			}),
		);
		await user.click(screen.getByRole("button", { name: "Run Cleanup" }));
		expect(cleanup).toHaveBeenCalledOnce();
	});
});
