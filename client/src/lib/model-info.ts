import { formatContextTokens } from "@/lib/run-usage";

interface ModelFacts {
	id: string;
	display_name: string;
	context_window?: number | null;
	input_price?: string | null;
	output_price?: string | null;
	reasoning_choices?: string[];
}

function formatPrice(price: string): string {
	const value = Number(price);
	if (!Number.isFinite(value)) return price;
	return `$${value >= 1 ? value.toFixed(2).replace(/\.00$/, "") : String(value)}`;
}

/**
 * One line of catalog facts for a model picker row: id (when it differs from
 * the name), context window, input/output price per million, and whether
 * reasoning is adjustable. Unknown facts are left out.
 */
export function modelFactsLine(model: ModelFacts): string | undefined {
	const parts = [
		model.id !== model.display_name ? model.id : null,
		model.context_window
			? `${formatContextTokens(model.context_window)} context`
			: null,
		model.input_price != null && model.output_price != null
			? `${formatPrice(model.input_price)} / ${formatPrice(model.output_price)} per 1M`
			: null,
		model.reasoning_choices?.length ? "reasoning" : null,
	].filter((part): part is string => part !== null);
	return parts.length ? parts.join(" · ") : undefined;
}

const REASONING_LABELS: Record<string, string> = {
	off: "Off",
	on: "On",
	none: "None",
	minimal: "Minimal",
	low: "Low",
	medium: "Medium",
	high: "High",
	xhigh: "Extra high",
	max: "Max",
};

export function reasoningLabel(choice: string): string {
	return REASONING_LABELS[choice] ?? choice;
}
