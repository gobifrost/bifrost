import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { CaseEditor } from "./CaseEditor";
import { agentPlatform } from "@/services/agentPlatform";
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: { createCase: vi.fn(), updateCase: vi.fn() },
}));
describe("case authoring", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});
	it("preserves nested mock results and freezes only on explicit save", async () => {
		vi.mocked(agentPlatform.createCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={saved} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Test name"), {
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
			screen.getByRole("button", { name: "Save test" }),
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
		fireEvent.submit(screen.getByRole("form", { name: "Add test" }));
		expect(await screen.findByText(/Your text is preserved/)).toBeVisible();
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
	});
	it("authors an ordinary case through guided controls without JSON", async () => {
		vi.mocked(agentPlatform.createCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={saved} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Test name"), {
			target: { value: "Guided lookup" },
		});
		fireEvent.change(screen.getByLabelText("Message"), {
			target: { value: "Look up the account." },
		});
		// Default expectations already cover completion + safety; add one
		// guided tool check through the controls.
		fireEvent.change(screen.getByLabelText("Add expectation"), {
			target: { value: "tool_called" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add" }));
		fireEvent.change(screen.getByLabelText("Expected tool"), {
			target: { value: "lookup" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Done" }));
		fireEvent.click(
			screen.getByRole("button", { name: "Save test" }),
		);
		await waitFor(() => expect(saved).toHaveBeenCalledOnce());
		expect(agentPlatform.createCase).toHaveBeenCalledWith(
			"suite",
			expect.objectContaining({
				name: "Guided lookup",
				input: { message: "Look up the account." },
				assertions: [
					{
						type: "terminal_status",
						label: "Run completes",
						params: { status: "completed" },
					},
					{
						type: "no_real_tools",
						label: "No real tools run",
						params: {},
					},
					{
						type: "tool_called",
						label: "Tool called",
						params: { tool: "lookup" },
					},
				],
			}),
		);
	});
	it("loads advanced definitions losslessly and keeps typed values", async () => {
		vi.mocked(agentPlatform.updateCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor
				suiteId="suite"
				onSaved={saved}
				onCancel={vi.fn()}
				draft={{
					id: "draft",
					suite_id: "suite",
					name: "Advanced draft",
					position: 0,
					enabled: true,
					version: 1,
					input: { message: "hi" },
					fixture: {},
					assertions: [
						{
							type: "output_path",
							label: "Count check",
							params: { path: "total", equals: 3 },
						},
						{
							type: "llm_judge",
							label: "Tone",
							params: {
								rubric: "polite",
								prompt_version: "v1",
								threshold: 0.7,
								judge_profile_id: "judge",
							},
						},
					],
					expected_tools: [],
					forbidden_tools: [],
					output_schema: null,
					repetitions: 1,
					scoring_policy: {},
					provenance: "manual",
					provenance_run_ids: [],
					tags: [],
					accepted: false,
				}}
			/>,
		);
		// llm_judge has no guided editor: shown as Advanced-only, kept whole.
		expect(screen.getByText("Tone")).toBeVisible();
		expect(screen.getByText(/Advanced detail/)).toBeVisible();
		fireEvent.click(
			screen.getByRole("button", { name: "Save test changes" }),
		);
		await waitFor(() => expect(saved).toHaveBeenCalledOnce());
		const body = vi.mocked(agentPlatform.updateCase).mock.calls[0][2] as {
			assertions: unknown[];
		};
		expect(body.assertions).toEqual([
			{
				type: "output_path",
				label: "Count check",
				params: { path: "total", equals: 3 },
			},
			{
				type: "llm_judge",
				label: "Tone",
				params: {
					rubric: "polite",
					prompt_version: "v1",
					threshold: 0.7,
					judge_profile_id: "judge",
				},
			},
		]);
	});
	it("keeps edits when Advanced JSON is invalid or the save fails", async () => {
		vi.mocked(agentPlatform.createCase).mockRejectedValueOnce(
			new Error("Network down"),
		);
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={vi.fn()} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Test name"), {
			target: { value: "Kept draft" },
		});
		fireEvent.change(screen.getByLabelText("Expectations (JSON)"), {
			target: { value: "[oops" },
		});
		expect(
			await screen.findByText(/Expectations JSON is invalid/),
		).toBeVisible();
		// Invalid text is preserved, not reset.
		expect(
			screen.getByLabelText("Expectations (JSON)") as HTMLTextAreaElement,
		).toHaveValue("[oops");
		fireEvent.click(screen.getByRole("button", { name: "Revert to last valid" }));
		fireEvent.change(screen.getByLabelText("Message"), {
			target: { value: "Still here." },
		});
		fireEvent.click(
			screen.getByRole("button", { name: "Save test" }),
		);
		await waitFor(() =>
			expect(screen.getByText("Network down")).toBeVisible(),
		);
		// Failed save keeps every field.
		expect(screen.getByLabelText("Test name")).toHaveValue("Kept draft");
		expect(screen.getByLabelText("Message")).toHaveValue("Still here.");
	});
	it("rejects malformed Advanced assertion entries without crashing", async () => {
		vi.mocked(agentPlatform.createCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={saved} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Test name"), {
			target: { value: "Shape checks" },
		});
		// [null] must not crash the renderers or replace the valid items.
		fireEvent.change(screen.getByLabelText("Expectations (JSON)"), {
			target: { value: "[null]" },
		});
		expect(
			await screen.findByText(/must be an object with a "type"/),
		).toBeVisible();
		expect(screen.getByText("Run completes")).toBeVisible();
		expect(screen.getByText("No real tools run")).toBeVisible();
		expect(
			screen.getByLabelText("Expectations (JSON)") as HTMLTextAreaElement,
		).toHaveValue("[null]");
		fireEvent.submit(screen.getByRole("form", { name: "Add test" }));
		// Submission is blocked: the shape error stays visible and nothing
		// is sent (the save guard rejects before any mutation runs).
		expect(
			await screen.findByText(/must be an object with a "type"/),
		).toBeVisible();
		await new Promise((resolve) => setTimeout(resolve, 50));
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
		// A missing type is equally rejected with the old items intact.
		fireEvent.change(screen.getByLabelText("Expectations (JSON)"), {
			target: { value: "[{}]" },
		});
		expect(
			await screen.findByText(/"type" must be a nonempty string/),
		).toBeVisible();
		expect(screen.getByText("Run completes")).toBeVisible();
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
		// Unknown but structurally valid entries (null label, extra fields)
		// are accepted and preserved whole.
		const advanced = JSON.stringify([
			{
				type: "terminal_status",
				label: "Run completes",
				params: { status: "completed" },
			},
			{
				type: "custom_check",
				label: null,
				params: { threshold: 2 },
				extra: true,
			},
		]);
		fireEvent.change(screen.getByLabelText("Expectations (JSON)"), {
			target: { value: advanced },
		});
		await waitFor(() =>
			expect(screen.queryByText(/Fix it or revert/)).not.toBeInTheDocument(),
		);
		expect(screen.getByText("custom_check")).toBeVisible();
		fireEvent.click(
			screen.getByRole("button", { name: "Save test" }),
		);
		await waitFor(() => expect(saved).toHaveBeenCalledOnce());
		expect(agentPlatform.createCase).toHaveBeenCalledWith(
			"suite",
			expect.objectContaining({
				assertions: [
					{
						type: "terminal_status",
						label: "Run completes",
						params: { status: "completed" },
					},
					{
						type: "custom_check",
						label: null,
						params: { threshold: 2 },
						extra: true,
					},
				],
			}),
		);
	});
	it("blocks Done and save while a guided value is invalid", async () => {
		vi.mocked(agentPlatform.createCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={saved} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Test name"), {
			target: { value: "Typed values" },
		});
		fireEvent.change(screen.getByLabelText("Add expectation"), {
			target: { value: "output_path" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add" }));
		fireEvent.change(screen.getByLabelText("Output field path"), {
			target: { value: "answer.text" },
		});
		fireEvent.change(screen.getByLabelText("Check"), {
			target: { value: "equals" },
		});
		fireEvent.change(screen.getByLabelText("Value type"), {
			target: { value: "number" },
		});
		// Empty is invalid: Done is blocked and the draft stays visible.
		expect(await screen.findByText("Enter a value.")).toBeVisible();
		expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
		fireEvent.change(screen.getByLabelText("Value"), {
			target: { value: "abc" },
		});
		expect(screen.getByText("Enter a finite number.")).toBeVisible();
		expect(screen.getByLabelText("Value") as HTMLInputElement).toHaveValue(
			"abc",
		);
		// Editing another field must not clear the invalid-value error.
		fireEvent.change(screen.getByLabelText("Label"), {
			target: { value: "Renamed" },
		});
		expect(screen.getByText("Enter a finite number.")).toBeVisible();
		expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
		// Whole-form submit must not send the stale params.
		fireEvent.submit(screen.getByRole("form", { name: "Add test" }));
		expect(
			await screen.findByText(/Fix the highlighted expectation error/),
		).toBeVisible();
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
		// Structured JSON values are validated the same way.
		fireEvent.change(screen.getByLabelText("Value type"), {
			target: { value: "json" },
		});
		fireEvent.change(screen.getByLabelText("Value"), {
			target: { value: "{oops" },
		});
		expect(await screen.findByText("Invalid JSON value.")).toBeVisible();
		expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
		// Correction re-enables Done and saves the corrected value.
		fireEvent.change(screen.getByLabelText("Value type"), {
			target: { value: "number" },
		});
		fireEvent.change(screen.getByLabelText("Value"), {
			target: { value: "42" },
		});
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Done" }),
			).not.toBeDisabled(),
		);
		fireEvent.click(screen.getByRole("button", { name: "Done" }));
		fireEvent.click(
			screen.getByRole("button", { name: "Save test" }),
		);
		await waitFor(() => expect(saved).toHaveBeenCalledOnce());
		expect(agentPlatform.createCase).toHaveBeenCalledWith(
			"suite",
			expect.objectContaining({
				assertions: [
					{
						type: "terminal_status",
						label: "Run completes",
						params: { status: "completed" },
					},
					{
						type: "no_real_tools",
						label: "No real tools run",
						params: {},
					},
					{
						type: "output_path",
						label: "Renamed",
						params: { path: "answer.text", equals: 42 },
					},
				],
			}),
		);
	});
	it("locks other rows while a guided value is invalid", async () => {
		vi.mocked(agentPlatform.createCase).mockResolvedValue({} as never);
		const saved = vi.fn();
		renderWithProviders(
			<CaseEditor suiteId="suite" onSaved={saved} onCancel={vi.fn()} />,
		);
		fireEvent.change(screen.getByLabelText("Test name"), {
			target: { value: "Locked rows" },
		});
		fireEvent.change(screen.getByLabelText("Add expectation"), {
			target: { value: "output_path" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add" }));
		fireEvent.change(screen.getByLabelText("Output field path"), {
			target: { value: "answer.text" },
		});
		fireEvent.change(screen.getByLabelText("Check"), {
			target: { value: "equals" },
		});
		fireEvent.change(screen.getByLabelText("Value type"), {
			target: { value: "number" },
		});
		fireEvent.change(screen.getByLabelText("Value"), {
			target: { value: "abc" },
		});
		expect(screen.getByText("Enter a finite number.")).toBeVisible();
		// Editing another expectation cannot clear the guard.
		fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
		expect(
			screen.queryByLabelText("Terminal status"),
		).not.toBeInTheDocument();
		expect(screen.getByText("Enter a finite number.")).toBeVisible();
		// Adding a new expectation cannot clear the guard either.
		fireEvent.change(screen.getByLabelText("Add expectation"), {
			target: { value: "tool_called" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add" }));
		expect(screen.queryByLabelText("Expected tool")).not.toBeInTheDocument();
		expect(screen.getByText("Enter a finite number.")).toBeVisible();
		// Removing an unrelated row cannot clear the guard.
		fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
		expect(screen.getByText("Run completes")).toBeVisible();
		expect(screen.getByText("Enter a finite number.")).toBeVisible();
		expect(screen.getByLabelText("Value") as HTMLInputElement).toHaveValue(
			"abc",
		);
		fireEvent.submit(screen.getByRole("form", { name: "Add test" }));
		expect(
			await screen.findByText(/Fix the highlighted expectation error/),
		).toBeVisible();
		expect(agentPlatform.createCase).not.toHaveBeenCalled();
		// Removing the invalid row itself is the explicit discard.
		const removes = screen.getAllByRole("button", { name: "Remove" });
		fireEvent.click(removes[removes.length - 1]);
		expect(screen.queryByText("Enter a finite number.")).not.toBeInTheDocument();
		expect(screen.queryByLabelText("Value")).not.toBeInTheDocument();
		expect(
			screen.queryByLabelText("Output field path"),
		).not.toBeInTheDocument();
		expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(2);
		// Ordinary actions work again: edit another row, then add and save.
		fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
		expect(screen.getByLabelText("Terminal status")).toBeVisible();
		fireEvent.click(screen.getByRole("button", { name: "Done" }));
		fireEvent.change(screen.getByLabelText("Add expectation"), {
			target: { value: "tool_called" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add" }));
		fireEvent.change(screen.getByLabelText("Expected tool"), {
			target: { value: "lookup" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Done" }));
		fireEvent.click(
			screen.getByRole("button", { name: "Save test" }),
		);
		await waitFor(() => expect(saved).toHaveBeenCalledOnce());
		expect(agentPlatform.createCase).toHaveBeenCalledWith(
			"suite",
			expect.objectContaining({
				assertions: expect.arrayContaining([
					{
						type: "tool_called",
						label: "Tool called",
						params: { tool: "lookup" },
					},
				]),
			}),
		);
	});
	it("summarizes expectation parameters under each row title", async () => {
		renderWithProviders(
			<CaseEditor
				suiteId="suite"
				onSaved={vi.fn()}
				onCancel={vi.fn()}
				draft={{
					id: "draft",
					suite_id: "suite",
					name: "Summarized",
					position: 0,
					enabled: true,
					version: 1,
					input: { message: "hi" },
					fixture: {},
					assertions: [
						{
							type: "tool_called",
							label: "Lookup used",
							params: { tool: "lookup" },
						},
						{
							type: "output_path",
							label: "Count check",
							params: { path: "total", equals: 3 },
						},
						{
							type: "max_tokens",
							label: "Token budget",
							params: { limit: 500 },
						},
					],
					expected_tools: [],
					forbidden_tools: [],
					output_schema: null,
					repetitions: 1,
					scoring_policy: {},
					provenance: "manual",
					provenance_run_ids: [],
					tags: [],
					accepted: false,
				}}
			/>,
		);
		expect(await screen.findByText("Must call lookup")).toBeVisible();
		expect(
			screen.getByText("Output total must equal 3"),
		).toBeVisible();
		expect(screen.getByText("Uses at most 500 tokens")).toBeVisible();
	});
});
