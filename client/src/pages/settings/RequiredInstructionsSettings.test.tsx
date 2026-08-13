import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.fn();
const updateSettings = vi.fn();

vi.mock("@/services/required-instructions", () => ({
	getRequiredInstructionsSettings: (organizationId?: string) =>
		getSettings(organizationId),
	updateRequiredInstructionsSettings: (
		instructions: string,
		organizationId?: string,
	) => updateSettings(instructions, organizationId),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		onChange,
		ariaLabel,
	}: {
		content: string;
		onChange: (value: string) => void;
		ariaLabel: string;
	}) => (
		<textarea
			aria-label={ariaLabel}
			value={content}
			onChange={(event) => onChange(event.target.value)}
		/>
	),
}));

import { RequiredInstructionsSettings } from "./RequiredInstructionsSettings";

describe("RequiredInstructionsSettings", () => {
	beforeEach(() => {
		getSettings.mockReset().mockResolvedValue({ instructions: "" });
		updateSettings.mockReset().mockImplementation((instructions) =>
			Promise.resolve({ instructions }),
		);
	});

	it("edits global required instructions", async () => {
		const user = userEvent.setup();
		render(<RequiredInstructionsSettings />);

		const editor = await screen.findByRole("textbox", {
			name: "Global Instructions editor",
		});
		await user.type(editor, "Always verify the customer.");
		await user.click(
			screen.getByRole("button", { name: "Save Instructions" }),
		);

		await waitFor(() =>
			expect(updateSettings).toHaveBeenCalledWith(
				"Always verify the customer.",
				undefined,
			),
		);
	});

	it("loads and saves an organization-specific scope", async () => {
		getSettings.mockResolvedValue({ instructions: "Use Acme's runbook." });
		const user = userEvent.setup();
		render(
			<RequiredInstructionsSettings organizationId="org-1" embedded />,
		);

		const editor = await screen.findByRole("textbox", {
			name: "Organization Instructions editor",
		});
		await user.type(editor, " Confirm approval.");
		await user.click(
			screen.getByRole("button", { name: "Save Instructions" }),
		);

		await waitFor(() =>
			expect(updateSettings).toHaveBeenCalledWith(
				"Use Acme's runbook. Confirm approval.",
				"org-1",
			),
		);
	});
});
