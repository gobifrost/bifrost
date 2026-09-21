import { describe, expect, it } from "vitest";
import { summarizeExpectation } from "./expectationSummaries";

describe("summarizeExpectation", () => {
	it("summarizes tool-call expectations with the tool name", () => {
		expect(
			summarizeExpectation({
				type: "tool_called",
				label: "Lookup used",
				params: { tool: "lookup" },
			}),
		).toBe("Must call lookup");
		expect(
			summarizeExpectation({
				type: "tool_not_called",
				params: { tool: "send_email" },
			}),
		).toBe("Must not call send_email");
	});

	it("summarizes terminal status and output checks with typed values", () => {
		expect(
			summarizeExpectation({
				type: "terminal_status",
				params: { status: "completed" },
			}),
		).toBe("Completes successfully");
		expect(
			summarizeExpectation({
				type: "terminal_status",
				params: { status: "failed" },
			}),
		).toBe("Completes with status failed");
		expect(
			summarizeExpectation({
				type: "output_path",
				params: { path: "customer.id", equals: 42 },
			}),
		).toBe("Output customer.id must equal 42");
		expect(
			summarizeExpectation({
				type: "output_path",
				params: { path: "answer.text" },
			}),
		).toBe("Output answer.text must be present");
	});

	it("summarizes maximums with actual units", () => {
		expect(
			summarizeExpectation({
				type: "max_tokens",
				params: { limit: 500 },
			}),
		).toBe("Uses at most 500 tokens");
		expect(
			summarizeExpectation({
				type: "no_real_tools",
				params: {},
			}),
		).toBe("Runs without real tool side effects");
	});

	it("returns null for unknown types and missing parameters", () => {
		expect(
			summarizeExpectation({
				type: "llm_judge",
				label: "Tone",
				params: { rubric: "polite" },
			}),
		).toBeNull();
		expect(
			summarizeExpectation({
				type: "tool_called",
				params: { tool: "  " },
			}),
		).toBeNull();
	});
});
