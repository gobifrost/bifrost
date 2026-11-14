import type { MarkdownManager } from "@tiptap/markdown";

// Compare Markdown structure, not incidental spelling (list markers, emphasis
// delimiters, or blank lines). Keep token metadata such as link targets, heading
// levels, task checkboxes, HTML, and reference definitions in the comparison.
function structure(value: unknown): unknown {
	if (Array.isArray(value)) {
		return value.filter((token) => token?.type !== "space").map(structure);
	}
	if (value && typeof value === "object") {
		const token = value as Record<string, unknown>;
		return Object.fromEntries(
			Object.keys(token)
				.sort()
				.filter(
					(key) =>
						!["raw", "loose", "escaped"].includes(key) &&
						!(key === "text" && Array.isArray(token.tokens)),
				)
				.map((key) => [key, structure(token[key])]),
		);
	}
	return value;
}

export function canEditMarkdownVisually(
	manager: MarkdownManager,
	source: string,
	serialized: string,
): boolean {
	const original = manager.instance.lexer(source);
	const roundTrip = manager.instance.lexer(serialized);
	return (
		JSON.stringify(structure(original)) ===
			JSON.stringify(structure(roundTrip)) &&
		JSON.stringify(structure(original.links)) ===
			JSON.stringify(structure(roundTrip.links))
	);
}
