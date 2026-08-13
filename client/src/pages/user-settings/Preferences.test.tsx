import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.fn();
const updateSettings = vi.fn();
const getMemories = vi.fn();
const deleteMemory = vi.fn();

vi.mock("@/services/memory", () => ({
	getUserMemorySettings: () => getSettings(),
	updateUserMemorySettings: (enabled: boolean) => updateSettings(enabled),
	listMemories: () => getMemories(),
	removeMemory: (memoryId: string) => deleteMemory(memoryId),
}));
vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		ariaLabel,
	}: {
		content: string;
		ariaLabel: string;
	}) => <article aria-label={ariaLabel}>{content}</article>,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { Preferences } from "./Preferences";

const settings = {
	platform_enabled: true,
	user_enabled: true,
	effective_enabled: true,
};

describe("Preferences", () => {
	beforeEach(() => {
		getSettings.mockReset().mockResolvedValue(settings);
		updateSettings.mockReset().mockResolvedValue({
			...settings,
			user_enabled: false,
			effective_enabled: false,
		});
		getMemories.mockReset().mockResolvedValue({
			count: 1,
			entries: [
				{
					id: "84c4c4cb-37d6-4fb1-9472-b266dd0e429a",
					content: "## Acme onboarding\nUse the customer checklist.",
					metadata: {},
					created_at: "2026-08-12T20:00:00Z",
					updated_at: "2026-08-12T20:00:00Z",
				},
			],
		});
		deleteMemory.mockReset().mockResolvedValue(undefined);
	});

	it("lets the user opt out and renders saved markdown through the viewer", async () => {
		const user = userEvent.setup();
		render(<Preferences />);

		const toggle = await screen.findByRole("switch", {
			name: "Enable Memory",
		});
		await waitFor(() => expect(toggle).toBeEnabled());
		expect(
			screen.getByRole("article", { name: "Saved memory" }),
		).toHaveTextContent("Acme onboarding");

		await user.click(toggle);
		await waitFor(() => expect(updateSettings).toHaveBeenCalledWith(false));
	});

	it("confirms before removing a saved memory", async () => {
		const user = userEvent.setup();
		render(<Preferences />);

		await user.click(
			await screen.findByRole("button", { name: "Remove memory" }),
		);
		await user.click(screen.getByRole("button", { name: /^Remove$/ }));

		await waitFor(() =>
			expect(deleteMemory).toHaveBeenCalledWith(
				"84c4c4cb-37d6-4fb1-9472-b266dd0e429a",
			),
		);
		expect(screen.queryByText(/Acme onboarding/)).not.toBeInTheDocument();
	});
});
