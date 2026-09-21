import type { components } from "@/lib/v1";

type AssertionDef = components["schemas"]["EvaluationAssertion"];

const MAX_UNITS: Record<string, string> = {
	max_iterations: "iterations",
	max_tokens: "tokens",
	max_cost_usd: "USD",
	max_latency_ms: "ms",
};

function formatValue(value: unknown): string {
	if (value === null) return "null";
	if (value === undefined) return "missing";
	if (typeof value === "string") return value || "empty";
	if (typeof value === "number" || typeof value === "boolean")
		return String(value);
	try {
		const text = JSON.stringify(value);
		return text ?? "unreadable value";
	} catch {
		return "unreadable value";
	}
}

/**
 * One-line human summary of an expectation's parameters, rendered under the
 * custom label in every expectation row and in saved-test inspection.
 * Unknown types return null so callers fall back to the raw type name.
 */
export function summarizeExpectation(def: AssertionDef): string | null {
	const params =
		def.params && typeof def.params === "object"
			? (def.params as Record<string, unknown>)
			: null;
	if (!params) return null;
	switch (def.type) {
		case "terminal_status":
			if (
				typeof params.status !== "string" ||
				!params.status.trim()
			)
				return null;
			return params.status === "completed"
				? "Completes successfully"
				: `Completes with status ${params.status}`;
		case "tool_called":
			return typeof params.tool === "string" && params.tool.trim()
				? `Must call ${params.tool}`
				: null;
		case "tool_not_called":
			return typeof params.tool === "string" && params.tool.trim()
				? `Must not call ${params.tool}`
				: null;
		case "output_path": {
			if (typeof params.path !== "string" || !params.path.trim())
				return null;
			if ("equals" in params)
				return `Output ${params.path} must equal ${formatValue(params.equals)}`;
			if ("contains" in params)
				return `Output ${params.path} must contain ${formatValue(params.contains)}`;
			return `Output ${params.path} must be present`;
		}
		case "max_iterations":
		case "max_tokens":
		case "max_cost_usd":
		case "max_latency_ms": {
			const limit = params.limit ?? params.max;
			if (typeof limit !== "number" || !Number.isFinite(limit))
				return null;
			return `Uses at most ${limit} ${MAX_UNITS[def.type] ?? "units"}`;
		}
		case "no_real_tools":
			return "Runs without real tool side effects";
		default:
			return null;
	}
}
