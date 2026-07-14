import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Eye, Copy, Check, TreeDeciduous } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { classify, tableColumns, MAX_TABLE_ROWS } from "./prettyShape";

const MAX_SCALAR_DISPLAY_CHARS = 1000;
const MAX_HIGHLIGHTED_JSON_CHARS = 25_000;

interface PrettyInputDisplayProps {
	inputData: Record<string, unknown> | unknown[];
	showToggle?: boolean;
	defaultView?: "pretty" | "tree";
}

/**
 * Convert snake_case to Title Case
 * Examples:
 * - user_name → User Name
 * - api_key → API Key
 * - first_name_last_name → First Name Last Name
 */
function snakeCaseToTitleCase(str: string): string {
	return str
		.split("_")
		.map((word) => {
			// Handle common acronyms
			const acronyms = [
				"api",
				"id",
				"url",
				"uri",
				"http",
				"https",
				"ip",
				"sql",
				"db",
				"ui",
				"ux",
			];
			if (acronyms.includes(word.toLowerCase())) {
				return word.toUpperCase();
			}
			// Capitalize first letter
			return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
		})
		.join(" ");
}

/**
 * Format a scalar value for display
 */
function formatScalar(value: unknown): { display: string; badge?: string } {
	if (value === null) {
		return { display: "null", badge: "null" };
	}

	if (value === undefined) {
		return { display: "undefined", badge: "undefined" };
	}

	if (typeof value === "boolean") {
		return {
			display: value ? "Yes" : "No",
			badge: value ? "true" : "false",
		};
	}

	if (typeof value === "number") {
		return { display: value.toLocaleString(), badge: "number" };
	}

	if (typeof value === "string") {
		if (value.length > MAX_SCALAR_DISPLAY_CHARS) {
			return {
				display: `${value.slice(0, MAX_SCALAR_DISPLAY_CHARS)}…`,
				badge: `${value.length.toLocaleString()} characters total`,
			};
		}

		// Check if it's a URL
		try {
			new URL(value);
			return { display: value, badge: "url" };
		} catch {
			// Check if it's a date
			const dateRegex = /^\d{4}-\d{2}-\d{2}/;
			if (dateRegex.test(value)) {
				try {
					const date = new Date(value);
					if (!isNaN(date.getTime())) {
						return {
							display: date.toLocaleString(),
							badge: "date",
						};
					}
				} catch {
					// Not a valid date
				}
			}

			// Regular string
			return { display: value };
		}
	}

	return { display: String(value) };
}

/** Badge text for a top-level row, derived from the value's shape. */
function badgeFor(value: unknown): string | undefined {
	if (Array.isArray(value)) {
		return `array (${value.length})`;
	}
	if (typeof value === "object" && value !== null) {
		// Nested rows speak for themselves; only badge the JSON fallback.
		return classify(value) === "flat-object" ? undefined : "object";
	}
	return formatScalar(value).badge;
}

/** Syntax-highlighted JSON block — the ladder's last resort. */
function JsonBlock({ value }: { value: unknown }) {
	const json = JSON.stringify(value, null, 2);
	if (json.length > MAX_HIGHLIGHTED_JSON_CHARS) {
		return (
			<div className="mt-1 space-y-1.5">
				<p className="text-xs text-muted-foreground">
					Showing the first {MAX_HIGHLIGHTED_JSON_CHARS.toLocaleString()} of{" "}
					{json.length.toLocaleString()} JSON characters. Copy retains the full
					value.
				</p>
				<pre className="max-h-64 max-w-full overflow-auto rounded-md bg-slate-950 p-3 font-mono text-xs text-slate-100">
					{json.slice(0, MAX_HIGHLIGHTED_JSON_CHARS)}
					{"\n…"}
				</pre>
			</div>
		);
	}

	return (
		<SyntaxHighlighter
			language="json"
			style={oneDark}
			customStyle={{
				margin: "0.25rem 0 0 0",
				borderRadius: "0.375rem",
				fontSize: "0.75rem",
				maxHeight: "16rem",
				maxWidth: "100%",
				overflow: "auto",
			}}
		>
			{json}
		</SyntaxHighlighter>
	);
}

/** Quiet in-panel mini table for arrays of same-shaped flat objects. */
function MiniTable({
	items,
	className,
}: {
	items: Array<Record<string, unknown>>;
	className?: string;
}) {
	const columns = tableColumns(items);
	if (columns === null) return <JsonBlock value={items} />;
	const previewItems = items.slice(0, MAX_TABLE_ROWS);

	return (
		<div className="space-y-1.5">
			{items.length > previewItems.length && (
				<p className="text-xs text-muted-foreground">
					Showing first {previewItems.length.toLocaleString()} of{" "}
					{items.length.toLocaleString()} rows
				</p>
			)}
			<div
				className={cn(
					"overflow-x-auto rounded-md ring-1 ring-foreground/5",
					className,
				)}
			>
			<table className="w-full text-sm">
				<thead>
					<tr className="bg-muted">
						{columns.map((col) => (
							<th
								key={col}
								className="px-2.5 py-1.5 text-left text-xs font-medium text-muted-foreground"
							>
								{snakeCaseToTitleCase(col)}
							</th>
						))}
					</tr>
				</thead>
				<tbody className="divide-y divide-border/60">
					{previewItems.map((item, i) => (
						<tr key={i}>
							{columns.map((col) => {
								const cell = item[col];
								return (
									<td
										key={col}
										className="px-2.5 py-1.5 align-top break-words"
									>
										{cell === null || cell === undefined ? (
											<span className="text-muted-foreground/60">
												—
											</span>
										) : (
											formatScalar(cell).display
										)}
									</td>
								);
							})}
						</tr>
					))}
				</tbody>
			</table>
			</div>
		</div>
	);
}

/** Nested label/value rows — the form idiom, one level deeper per depth. */
function ObjectRows({
	data,
	depth,
}: {
	data: Record<string, unknown>;
	depth: number;
}) {
	const entries = Object.entries(data);
	if (entries.length === 0) {
		return <p className="italic text-muted-foreground/70">Empty object</p>;
	}

	return (
		<div className="mt-1 space-y-1.5 border-l border-border/60 pl-3">
			{entries.map(([key, value]) => (
				<div key={key}>
					<label className="text-xs font-medium text-foreground/80">
						{snakeCaseToTitleCase(key)}
					</label>
					<div className="text-sm text-muted-foreground">
						<ValueContent value={value} depth={depth + 1} />
					</div>
				</div>
			))}
		</div>
	);
}

/**
 * Render a value by the ladder: scalar row → nested rows → inline list →
 * mini table → JSON block (last resort).
 */
function ValueContent({ value, depth }: { value: unknown; depth: number }) {
	const shape = classify(value, depth);

	switch (shape) {
		case "scalar":
			return (
				<p className="whitespace-pre-wrap break-all">
					{formatScalar(value).display}
				</p>
			);
		case "scalar-array": {
			const items = value as unknown[];
			if (items.length === 0) {
				return (
					<p className="italic text-muted-foreground/70">
						Empty list
					</p>
				);
			}
			return (
				<p className="whitespace-pre-wrap break-words">
					{items.map((v) => formatScalar(v).display).join(", ")}
				</p>
			);
		}
		case "flat-object":
			return (
				<ObjectRows
					data={value as Record<string, unknown>}
					depth={depth}
				/>
			);
		case "object-table":
			return (
				<MiniTable
					items={value as Array<Record<string, unknown>>}
					className="mt-1"
				/>
			);
		case "json":
			return <JsonBlock value={value} />;
	}
}

export function PrettyInputDisplay({
	inputData,
	showToggle = false,
	defaultView = "pretty",
}: PrettyInputDisplayProps) {
	const [view, setView] = useState<"pretty" | "tree">(defaultView);
	const [copied, setCopied] = useState(false);

	const handleCopy = async () => {
		try {
			await navigator.clipboard.writeText(
				JSON.stringify(inputData, null, 2),
			);
			setCopied(true);
			toast.success("Copied to clipboard");
			setTimeout(() => setCopied(false), 2000);
		} catch {
			toast.error("Failed to copy to clipboard");
		}
	};

	// Tree view
	if (view === "tree") {
		return (
			<div className="space-y-2">
				<div className="flex items-center justify-between">
					{showToggle ? (
						<p className="text-xs text-muted-foreground">
							Viewing tree structure
						</p>
					) : (
						<div />
					)}
					<div className="flex gap-1.5">
						<Button
							variant="outline"
							size="sm"
							className="h-7 px-2.5 text-xs"
							onClick={handleCopy}
						>
							{copied ? (
								<Check className="mr-1.5 h-3.5 w-3.5" />
							) : (
								<Copy className="mr-1.5 h-3.5 w-3.5" />
							)}
							{copied ? "Copied!" : "Copy"}
						</Button>
						{showToggle && (
							<Button
								variant="outline"
								size="sm"
								className="h-7 px-2.5 text-xs"
								onClick={() => setView("pretty")}
							>
								<Eye className="mr-1.5 h-3.5 w-3.5" />
								Pretty View
							</Button>
						)}
					</div>
				</div>
				<div className="rounded-lg ring-1 ring-foreground/5 p-3 bg-muted/50">
					<VariablesTreeView
						data={inputData as Record<string, unknown>}
					/>
				</div>
			</div>
		);
	}

	// Pretty view
	const isTopLevelArray = Array.isArray(inputData);
	const entries = Object.entries(inputData);

	if (entries.length === 0) {
		return (
			<div className="text-center text-muted-foreground py-8">
				{isTopLevelArray ? "No items" : "No input parameters"}
			</div>
		);
	}

	const countLine = isTopLevelArray
		? `${inputData.length} item${inputData.length !== 1 ? "s" : ""}`
		: `Viewing ${entries.length} parameter${entries.length !== 1 ? "s" : ""}`;

	const toggleBar = showToggle && (
		<div className="flex items-center justify-between">
			<p className="text-xs text-muted-foreground">{countLine}</p>
			<Button
				variant="outline"
				size="sm"
				className="h-7 px-2.5 text-xs"
				onClick={() => setView("tree")}
			>
				<TreeDeciduous className="mr-1.5 h-3.5 w-3.5" />
				Tree View
			</Button>
		</div>
	);

	// Top-level array: frame honestly ("5 items") and render the array itself
	// by the ladder — a table-shaped array becomes the table directly.
	if (isTopLevelArray) {
		const shape = classify(inputData);
		return (
			<div className="space-y-2">
				{toggleBar}
				{shape === "object-table" ? (
					<MiniTable
						items={inputData as Array<Record<string, unknown>>}
						className="rounded-lg bg-muted/50 ring-foreground/5"
					/>
				) : shape === "scalar-array" ? (
					<div className="rounded-lg ring-1 ring-foreground/5 bg-muted/50 px-3 py-2.5 text-sm text-muted-foreground">
						<ValueContent value={inputData} depth={0} />
					</div>
				) : (
					<JsonBlock value={inputData} />
				)}
			</div>
		);
	}

	return (
		<div className="space-y-2">
			{toggleBar}

			<div className="divide-y divide-border/60 overflow-hidden rounded-lg ring-1 ring-foreground/5 bg-muted/50">
				{entries.map(([key, value]) => {
					const friendlyLabel = snakeCaseToTitleCase(key);
					const badge = badgeFor(value);

					return (
						<div
							key={key}
							className="flex items-start gap-4 px-3 py-2.5 hover:bg-muted/50 transition-colors"
						>
							<div className="flex-1 min-w-0">
								<div className="flex items-center gap-2 mb-0.5">
									<label className="text-sm font-medium">
										{friendlyLabel}
									</label>
									{badge && (
										<Badge
											variant="secondary"
											className="text-xs"
										>
											{badge}
										</Badge>
									)}
								</div>
								<div className="text-sm text-muted-foreground break-words">
									<ValueContent value={value} depth={0} />
								</div>
							</div>
						</div>
					);
				})}
			</div>
		</div>
	);
}
