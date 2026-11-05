import { describe, it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { TuningDryRunResults } from "./TuningDryRunResults";

describe("TuningDryRunResults", () => {
	it("excludes evaluation failures from both behavior-change totals", () => {
		renderWithProviders(
			<TuningDryRunResults
				results={[
					{
						run_id: "failed",
						would_still_decide_same: false,
						evaluation_failed: true,
						reasoning: "Unable to evaluate",
						confidence: 0,
					},
					{
						run_id: "changed",
						evaluation_failed: false,
						would_still_decide_same: false,
						reasoning: "Ask first",
						confidence: 0.8,
					},
					{
						run_id: "same",
						evaluation_failed: false,
						would_still_decide_same: true,
						reasoning: "Same guidance",
						confidence: 0.9,
					},
				]}
			/>,
		);
		expect(
			screen.getByText(/1 of 2 evaluated runs would change behavior/),
		).toHaveTextContent("1 evaluation failed");
		expect(screen.getByText("Evaluation Failed")).toBeInTheDocument();
		expect(screen.queryByText("Confidence: 0%")).not.toBeInTheDocument();
		expect(screen.getAllByText("Would change")).toHaveLength(1);
	});
});
