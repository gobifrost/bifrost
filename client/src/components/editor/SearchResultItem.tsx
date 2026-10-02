import type { SearchMatch } from "@/services/searchService";

export function SearchResultItem({
	result,
	query,
	caseSensitive,
	useRegex,
	onClick,
	disabled = false,
}: {
	result: SearchMatch;
	query: string;
	caseSensitive: boolean;
	useRegex: boolean;
	onClick: () => void;
	disabled?: boolean;
}) {
	// Regex results retain the complete line; don't re-execute server regexes in the browser.
	const text = result.text;
	const readOnly = !result.source.editable;
	const source = caseSensitive ? text : text.toLowerCase();
	const needle = caseSensitive ? query : query.toLowerCase();
	const parts = [];
	let cursor = 0;
	if (needle && !useRegex) {
		let index = source.indexOf(needle);
		while (index >= 0) {
			parts.push(text.slice(cursor, index));
			parts.push(
				<mark
					key={index}
					className="rounded-sm bg-primary/20 text-foreground"
				>
					{text.slice(index, index + query.length)}
				</mark>,
			);
			cursor = index + query.length;
			index = source.indexOf(needle, cursor);
		}
	}
	parts.push(text.slice(cursor));
	return (
		<button
			type="button"
			disabled={disabled || readOnly}
			title={
				readOnly
					? "Deploy-owned Solution source — edit it locally and run bifrost solution deploy"
					: undefined
			}
			onClick={onClick}
			className="disabled:opacity-50 min-h-11 w-full min-w-0 space-y-2 p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none"
		>
			<span className="block text-sm leading-5 sm:text-xs text-muted-foreground [overflow-wrap:anywhere]">
				{result.file_path}
			</span>
			<span className="block text-xs text-muted-foreground">
				Line {result.line}
				{readOnly &&
					` · Solution · ${result.source.solution_slug} · read-only`}
			</span>
			<span className="block whitespace-pre-wrap font-mono text-sm leading-6 sm:text-xs sm:leading-5 [overflow-wrap:anywhere]">
				{parts}
			</span>
		</button>
	);
}
