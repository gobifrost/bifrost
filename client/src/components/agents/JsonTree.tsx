/**
 * JsonTree — compact, syntax-highlighted, collapsible JSON viewer.
 *
 * Used everywhere we render structured data inline (tool call args, tool
 * results, step content). Key design points:
 *
 *  - Each object/array is collapsible with a chevron — first level open by
 *    default, deeper levels closed so a deep payload doesn't blow up the row.
 *  - All wrapping handled by `break-all` + flex; never causes its parent
 *    to grow horizontally. Safe to drop into a min-w-0 flex column.
 *  - Tokens are color-coded (string / number / boolean / null / key) using
 *    Tailwind utility classes so it inherits the theme's dark/light mode.
 *  - Strings render with quotes and a "copy" affordance on long values;
 *    nothing fancy, just enough that an admin can grab an ID.
 *
 * Not a fully general JSON5 / JSONC parser — only renders what
 * `JSON.stringify` can produce, plus `undefined` (rendered as null).
 */

import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

export interface JsonTreeProps {
	value: unknown;
	/** Open the root-level container by default. Default: true. */
	defaultOpen?: boolean;
	/** Close everything beneath this depth on first render. Default: 1. */
	openDepth?: number;
	className?: string;
}

export function JsonTree({
	value,
	defaultOpen = true,
	openDepth = 1,
	className,
}: JsonTreeProps) {
	return (
		<div
			className={cn(
				"@container font-mono text-[13px] leading-6 text-foreground/90",
				className,
			)}
		>
			<Node
				value={value}
				depth={0}
				openDepth={openDepth}
				forceOpen={defaultOpen}
			/>
		</div>
	);
}

interface NodeProps {
	value: unknown;
	depth: number;
	openDepth: number;
	forceOpen?: boolean;
}

function Node({ value, depth, openDepth, forceOpen }: NodeProps) {
	if (value === null || value === undefined) {
		return <NullToken />;
	}
	if (typeof value === "boolean") {
		return <BoolToken value={value} />;
	}
	if (typeof value === "number") {
		return <NumberToken value={value} />;
	}
	if (typeof value === "string") {
		return <StringToken value={value} />;
	}
	if (Array.isArray(value)) {
		return (
			<Container
				kind="array"
				items={value.map((v, i) => ({ key: String(i), value: v }))}
				depth={depth}
				openDepth={openDepth}
				forceOpen={forceOpen}
			/>
		);
	}
	if (typeof value === "object") {
		const entries = Object.entries(value as Record<string, unknown>);
		return (
			<Container
				kind="object"
				items={entries.map(([k, v]) => ({ key: k, value: v }))}
				depth={depth}
				openDepth={openDepth}
				forceOpen={forceOpen}
			/>
		);
	}
	return <span>{String(value)}</span>;
}

interface ContainerProps {
	kind: "object" | "array";
	items: Array<{ key: string; value: unknown }>;
	depth: number;
	openDepth: number;
	forceOpen?: boolean;
}

function Container({ kind, items, depth, openDepth, forceOpen }: ContainerProps) {
	const [open, setOpen] = useState(
		forceOpen ?? depth < openDepth,
	);
	const open_b = kind === "object" ? "{" : "[";
	const close_b = kind === "object" ? "}" : "]";

	if (items.length === 0) {
		return (
			<span className="text-muted-foreground">
				{open_b}
				{close_b}
			</span>
		);
	}

	return (
		<div className="break-words">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="inline-flex min-h-11 items-center gap-1 rounded-[var(--bf-radius-control)] px-2 py-1.5 align-baseline text-muted-foreground transition-colors motion-reduce:transition-none hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
				aria-label={open ? "Collapse" : "Expand"}
			>
				<ChevronRight
					className={cn(
						"h-3.5 w-3.5 transition-transform motion-reduce:transition-none",
						open && "rotate-90",
					)}
				/>
				<span>{open_b}</span>
				{!open ? (
					<span className="ml-1 text-[11px] text-muted-foreground/70">
						{items.length}{" "}
						{kind === "array" ? "items" : "keys"}
					</span>
				) : null}
			</button>
			{open ? (
				<ul className="ml-1 border-l border-border/40 pl-2 @sm:ml-3 @sm:pl-3">
					{items.map((it) => (
						<Row
							key={it.key}
							kind={kind}
							entry={it}
							depth={depth}
							openDepth={openDepth}
						/>
					))}
				</ul>
			) : null}
			<span className="text-muted-foreground">{close_b}</span>
		</div>
	);
}

interface RowProps {
	kind: "object" | "array";
	entry: { key: string; value: unknown };
	depth: number;
	openDepth: number;
}

function Row({ kind, entry, depth, openDepth }: RowProps) {
	return (
		<li className="min-w-0 items-baseline gap-2 [overflow-wrap:anywhere] @sm:flex">
			{kind === "object" ? (
				<span className="text-primary">
					&quot;{entry.key}&quot;
				</span>
			) : (
				<span className="text-muted-foreground/60">{entry.key}:</span>
			)}
			{kind === "object" ? (
				<span className="text-muted-foreground">:</span>
			) : null}
			<span className="block min-w-0 flex-1 [overflow-wrap:anywhere]">
				<Node
					value={entry.value}
					depth={depth + 1}
					openDepth={openDepth}
				/>
			</span>
		</li>
	);
}

function NullToken() {
	return <span className="text-muted-foreground italic">null</span>;
}

function BoolToken({ value }: { value: boolean }) {
	return (
		<span className="text-warning">
			{String(value)}
		</span>
	);
}

function NumberToken({ value }: { value: number }) {
	return (
		<span className="text-power">
			{String(value)}
		</span>
	);
}

function StringToken({ value }: { value: string }) {
	// Long strings wrap; short ones render inline.
	return (
		<span className="break-words text-success">
			&quot;{value}&quot;
		</span>
	);
}

/** Helper for callers that just want a one-line "preview" on a collapsed
 * row (e.g. a sidebar). Returns a trivially short string for primitives,
 * otherwise an items count. */
export function jsonPreview(value: unknown): string {
	if (value === null || value === undefined) return "null";
	if (typeof value === "string") return `"${value.slice(0, 60)}"`;
	if (typeof value === "number" || typeof value === "boolean")
		return String(value);
	if (Array.isArray(value)) return `[${value.length}]`;
	if (typeof value === "object")
		return `{${Object.keys(value as Record<string, unknown>).length}}`;
	return String(value);
}

/** Helper: true iff `value` is an empty container ({} or []) or null/undefined. */
export function isEmptyJson(value: unknown): boolean {
	if (value === null || value === undefined) return true;
	if (typeof value === "string") return value.length === 0;
	if (Array.isArray(value)) return value.length === 0;
	if (typeof value === "object")
		return Object.keys(value as Record<string, unknown>).length === 0;
	return false;
}

// ReactNode export so consumers can ergonomically use the children prop pattern.
export type JsonTreeChildren = ReactNode;
