import { describe, expect, it } from "vitest";

import { modelFactsLine, reasoningLabel } from "./model-info";

describe("modelFactsLine", () => {
	it("summarizes catalog facts in picker order", () => {
		expect(
			modelFactsLine({
				id: "claude-haiku-4-5",
				display_name: "Claude Haiku 4.5",
				context_window: 200_000,
				input_price: "1",
				output_price: "5",
				reasoning_choices: ["off", "on"],
			}),
		).toBe("claude-haiku-4-5 · 200k context · $1 / $5 per 1M · reasoning");
	});

	it("keeps sub-dollar prices exact and omits unknown facts", () => {
		expect(
			modelFactsLine({
				id: "deepseek/deepseek-v4-flash",
				display_name: "deepseek/deepseek-v4-flash",
				input_price: "0.018",
				output_price: "0.32",
			}),
		).toBe("$0.018 / $0.32 per 1M");
		expect(modelFactsLine({ id: "m", display_name: "m" })).toBeUndefined();
	});
});

describe("reasoningLabel", () => {
	it("labels known levels and passes unknown ones through", () => {
		expect(reasoningLabel("xhigh")).toBe("Extra high");
		expect(reasoningLabel("off")).toBe("Off");
		expect(reasoningLabel("turbo")).toBe("turbo");
	});
});
