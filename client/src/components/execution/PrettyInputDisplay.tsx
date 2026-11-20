import { useState } from "react";
import { InputDisplayToolbar } from "./InputDisplayToolbar";
import { Badge } from "@/components/ui/badge";
import { JsonValuePreview } from "./JsonValuePreview";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { cn } from "@/lib/utils";
import { classify, tableColumns, MAX_TABLE_ROWS } from "./prettyShape";

const MAX_SCALAR_DISPLAY_CHARS = 1000;

interface PrettyInputDisplayProps {
	inputData: Record<string, unknown> | unknown[];
	showToggle?: boolean;
	showDescription?: boolean;
	defaultView?: "pretty" | "tree";
	context?: "input" | "result";
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

/** Quiet in-panel mini table for arrays of same-shaped flat objects. */
function MiniTable({
	items,
	className,
}: {
	items: Array<Record<string, unknown>>;
	className?: string;
}) {
	const columns = tableColumns(items);
	if (columns === null) return <JsonValuePreview value={items} />;
	const previewItems = items.slice(0, MAX_TABLE_ROWS);

	return (
		<div className="@container min-w-0 space-y-1.5">
			{items.length > previewItems.length && (
				<p className="text-xs text-muted-foreground">
					Showing first {previewItems.length.toLocaleString()} of{" "}
					{items.length.toLocaleString()} rows
				</p>
			)}
			<ol
				aria-label="Input records"
				className="divide-y divide-border rounded-[var(--bf-radius-surface)] border border-border bg-background/60 @2xl:hidden"
			>
				{previewItems.map((item, index) => (
					<InputArrayRecord
						key={index}
						item={item}
						columns={columns}
						index={index}
					/>
				))}
			</ol>
			<div
				className={cn(
					"hidden overflow-x-auto rounded-[var(--bf-radius-surface)] border border-border bg-background/60 @2xl:block",
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
											{cell === null ||
											cell === undefined ? (
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

function InputArrayRecord({
	item,
	columns,
	index,
}: {
	item: Record<string, unknown>;
	columns: string[];
	index: number;
}) {
	return (
		<li className="min-w-0 px-3 py-3">
			<p className="mb-3 text-xs font-medium text-muted-foreground">
				Item {index + 1}
			</p>
			<dl className="space-y-3">
				{columns.map((column) => (
					<div key={column}>
						<dt className="text-xs font-medium [overflow-wrap:anywhere]">
							{snakeCaseToTitleCase(column)}
						</dt>
						<dd className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground [overflow-wrap:anywhere]">
							{item[column] == null
								? "—"
								: formatScalar(item[column]).display}
						</dd>
					</div>
				))}
			</dl>
		</li>
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
				<p className="whitespace-pre-wrap [overflow-wrap:anywhere]">
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
				<p className="whitespace-pre-wrap [overflow-wrap:anywhere]">
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
			return <JsonValuePreview value={value} />;
	}
}

export function PrettyInputDisplay({
	inputData,
	showToggle = false,
	showDescription = true,
	defaultView = "pretty",
	context = "input",
}: PrettyInputDisplayProps) {
	const [view, setView] = useState<"pretty" | "tree">(defaultView);

	// Tree view
	if (view === "tree") {
		return (
			<div className="min-w-0 space-y-2">
				<InputDisplayToolbar
					inputData={inputData}
					view={view}
					showToggle={showToggle}
					description={
						showToggle && showDescription
							? "Viewing tree structure"
							: undefined
					}
					onViewChange={setView}
				/>
				<div
					data-pii-surface
					className="min-w-0 rounded-[var(--bf-radius-surface)] border border-border bg-muted/40 p-2 sm:p-3"
				>
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
				{isTopLevelArray
					? "No items"
					: context === "result"
						? "No result fields"
						: "No input parameters"}
			</div>
		);
	}

	const countLine = isTopLevelArray
		? `${inputData.length} item${inputData.length !== 1 ? "s" : ""}`
		: context === "result"
			? `Viewing ${entries.length} result field${entries.length !== 1 ? "s" : ""}`
			: `Viewing ${entries.length} parameter${entries.length !== 1 ? "s" : ""}`;

	const toggleBar = (
		<InputDisplayToolbar
			inputData={inputData}
			view={view}
			showToggle={showToggle}
			description={showToggle && showDescription ? countLine : undefined}
			onViewChange={setView}
		/>
	);

	// Top-level array: frame honestly ("5 items") and render the array itself
	// by the ladder — a table-shaped array becomes the table directly.
	if (isTopLevelArray) {
		const shape = classify(inputData);
		return (
			<div className="min-w-0 space-y-2">
				{toggleBar}
				<div data-pii-surface>
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
						<JsonValuePreview value={inputData} />
					)}
				</div>
			</div>
		);
	}

	return (
		<div className="min-w-0 space-y-2">
			{toggleBar}

			<div
				data-pii-surface
				className="@container min-w-0 divide-y divide-border rounded-[var(--bf-radius-surface)] border border-border bg-muted/40"
			>
				{entries.map(([key, value]) => {
					const friendlyLabel = snakeCaseToTitleCase(key);
					const badge = badgeFor(value);
					const scalar = classify(value) === "scalar";

					return (
						<div
							key={key}
							className="min-w-0 px-3 py-3 sm:px-4 sm:py-3.5"
						>
							<div
								className={cn(
									"min-w-0",
									scalar &&
										"@lg:grid @lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] @lg:gap-6",
								)}
							>
								<div className="flex flex-wrap items-center gap-2 mb-2">
									<span className="text-sm font-semibold text-primary [overflow-wrap:anywhere]">
										{friendlyLabel}
									</span>
									{badge && (
										<Badge
											variant="secondary"
											className="bg-background/70 text-xs font-normal text-muted-foreground"
										>
											{badge}
										</Badge>
									)}
								</div>
								<div className="min-w-0 text-sm leading-relaxed text-foreground break-words">
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
