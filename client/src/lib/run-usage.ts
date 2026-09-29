import { formatCost } from "@/lib/utils";
import type { components } from "@/lib/v1";

export type RunUsageSummary = components["schemas"]["AgentRunUsageSummary"];

/** Compact token count for context sizes: 950 → "950", 18_148 → "18.1k". */
export function formatContextTokens(tokens: number): string {
	if (tokens < 1_000) return String(tokens);
	if (tokens < 1_000_000) {
		const thousands = tokens / 1_000;
		return `${thousands < 100 ? thousands.toFixed(1).replace(/\.0$/, "") : Math.round(thousands)}k`;
	}
	return `${(tokens / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** Share of input served from the provider's prompt cache, as a whole percent. */
export function formatCacheRate(rate: number): string {
	return `${Math.round(rate * 100)}%`;
}

/**
 * The per-run usage line: cost, peak context, and cache hit rate, in that
 * order. Missing measurements are omitted rather than shown as zero.
 */
export function runUsageParts(
	summary: RunUsageSummary | null | undefined,
): string[] {
	if (!summary) return [];
	return [
		summary.cost != null ? formatCost(summary.cost) : null,
		summary.peak_context_tokens != null
			? `${formatContextTokens(summary.peak_context_tokens)} context`
			: null,
		summary.cache_hit_rate != null
			? `${formatCacheRate(summary.cache_hit_rate)} cached`
			: null,
	].filter((part): part is string => part !== null);
}

export const RUN_USAGE_HELP =
	"Cost includes delegated agents. Context is the largest single request this " +
	"run sent to the model. Cached is the share of input read from the " +
	"provider's prompt cache (billed at a reduced rate).";
