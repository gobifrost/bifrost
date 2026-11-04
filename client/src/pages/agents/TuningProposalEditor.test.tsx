import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { TuningProposalEditor } from "./TuningProposalEditor";
import { AGENT_PROMPT_MAX_LENGTH } from "@/lib/agent-prompt";

describe("TuningProposalEditor", () => {
	it("retains an editor-valid prompt longer than 20,000 characters", async () => {
		const prompt = "Ask before routing.\n".repeat(1500);
		const { user } = renderWithProviders(
			<TuningProposalEditor
				currentPrompt={prompt}
				edits={prompt}
				busy={false}
				applying={false}
				onChange={vi.fn()}
				onDiscard={vi.fn()}
				onApply={vi.fn()}
			/>,
		);
		await user.click(screen.getByRole("tab", { name: "Edit" }));
		expect(screen.getByLabelText("Proposed prompt (editable)")).toHaveValue(
			prompt,
		);
		expect(
			screen.getByLabelText("Proposed prompt (editable)"),
		).toHaveAttribute("maxlength", String(AGENT_PROMPT_MAX_LENGTH));
		await user.click(screen.getByRole("tab", { name: "Diff" }));
		await user.click(screen.getByRole("tab", { name: "Edit" }));
		expect(screen.getByLabelText("Proposed prompt (editable)")).toHaveValue(
			prompt,
		);
		expect(
			screen.getByRole("button", { name: /apply live/i }),
		).toBeEnabled();
	});
});
