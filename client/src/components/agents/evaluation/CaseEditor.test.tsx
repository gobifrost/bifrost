import { describe, expect, it, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { CaseEditor } from "./CaseEditor";
import { agentPlatform } from "@/services/agentPlatform";
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: { createCase: vi.fn(), updateCase: vi.fn() },
}));
describe("case authoring", () => {
	it("preserves nested mock results and freezes only on explicit save", async () => {
		vi.mocked(agentPlatform.createCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={saved} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Case name"), {
			target: { value: "Empty lookup" },
		});
		fireEvent.change(screen.getByLabelText("Tool name"), {
			target: { value: "lookup" },
		});
		fireEvent.change(screen.getByLabelText("Returned result (JSON)"), {
			target: { value: '{"data":{"items":[]}}' },
		});
		fireEvent.click(
			screen.getByRole("button", { name: "Add response rule" }),
		);
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
		fireEvent.click(
			screen.getByRole("button", { name: "Freeze and add case" }),
		);
		await waitFor(() => expect(saved).toHaveBeenCalledOnce());
		expect(agentPlatform.createCase).toHaveBeenCalledWith(
			"suite",
			expect.objectContaining({
				fixture: expect.objectContaining({
					rules: [
						{
							tool: "lookup",
							match_args: {},
							return: { data: { items: [] } },
						},
					],
				}),
			}),
		);
	});
	it("reports malformed input without sending a case", async () => {
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={vi.fn()} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Invocation input (JSON)"), {
			target: { value: "[]" },
		});
		fireEvent.submit(screen.getByRole("form", { name: "Author case" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Invocation input must be a JSON object",
		);
	});
});
