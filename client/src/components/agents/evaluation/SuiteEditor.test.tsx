import { expect, it, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { SuiteEditor } from "./SuiteEditor";
import { agentPlatform } from "@/services/agentPlatform";
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: { updateSuite: vi.fn().mockResolvedValue({}) },
}));
it("guards draft edits with the version that was reviewed", async () => {
	const saved = vi.fn();
	renderWithProviders(
		<SuiteEditor
			suite={{ id: "suite", name: "Old", status: "draft", version: 3 }}
			onSaved={saved}
		/>,
	);
	fireEvent.click(screen.getByText("Edit suite details"));
	fireEvent.change(screen.getByLabelText("Suite name"), {
		target: { value: "New" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Save suite details" }));
	await waitFor(() => expect(saved).toHaveBeenCalledOnce());
	expect(agentPlatform.updateSuite).toHaveBeenCalledWith("suite", {
		name: "New",
		description: "",
		expected_version: 3,
	});
});
