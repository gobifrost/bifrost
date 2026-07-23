/**
 * Render summarizer prose without leaking executor identifiers into the
 * normal review surface. Verified `[exact_tool_name]` markers can link to
 * their grouped Activity item; unmatched legacy markers remain ordinary
 * human-readable text instead of pretending to be controls.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import {
	activityDomId,
	delegationTarget,
	humanizeToolReference,
	type RunActivityReferenceIndex,
} from "./run-activity";

export interface DidNarrativeProps {
	text: string | null | undefined;
	/** Recorded actions keyed by exact tool name and ordered by occurrence. */
	activityReferences?: Readonly<RunActivityReferenceIndex>;
	onReferencePreview?: (activityId: string | null) => void;
	onReferenceActivate?: (activityId: string) => void;
	/** When true (drawer/sheet variants), use compact spacing. */
	compact?: boolean;
	fallback?: ReactNode;
}

const TOOL_MARKER = /\[([a-zA-Z_][a-zA-Z0-9_.-]*)\]/g;

type NarrativePart =
	{ kind: "text"; value: string } | { kind: "tool"; name: string };

function splitOnMarkers(text: string): NarrativePart[] {
	const parts: NarrativePart[] = [];
	let cursor = 0;
	for (const match of text.matchAll(TOOL_MARKER)) {
		const start = match.index ?? 0;
		if (start > cursor) {
			parts.push({ kind: "text", value: text.slice(cursor, start) });
		}
		parts.push({ kind: "tool", name: match[1] });
		cursor = start + match[0].length;
	}
	if (cursor < text.length) {
		parts.push({ kind: "text", value: text.slice(cursor) });
	}
	return parts;
}

export function DidNarrative({
	text,
	activityReferences,
	onReferencePreview,
	onReferenceActivate,
	compact,
	fallback,
}: DidNarrativeProps) {
	if (!text || !text.trim()) return <>{fallback}</>;
	const parts = splitOnMarkers(text);
	const occurrenceByTool: Record<string, number> = {};

	return (
		<div
			className={cn(
				"whitespace-pre-wrap break-words leading-relaxed",
				compact ? "text-xs" : "text-sm",
			)}
		>
			{parts.map((part, index) => {
				if (part.kind === "text")
					return <span key={index}>{part.value}</span>;
				const delegated = !!delegationTarget(part.name);
				const occurrence = occurrenceByTool[part.name] ?? 0;
				occurrenceByTool[part.name] = occurrence + 1;
				const reference = activityReferences?.[part.name]?.[occurrence];
				const label =
					reference?.label ?? humanizeToolReference(part.name);

				if (!reference || !onReferenceActivate) {
					return (
						<span
							key={index}
							data-slot="activity-reference-label"
							className="font-medium text-foreground/80"
						>
							{label}
						</span>
					);
				}

				return (
					<a
						key={index}
						href={`#${activityDomId(reference.activityId)}`}
						data-slot="activity-reference"
						data-activity-reference-id={reference.activityId}
						aria-label={`Show ${label} in Activity`}
						onMouseEnter={() =>
							onReferencePreview?.(reference.activityId)
						}
						onMouseLeave={() => onReferencePreview?.(null)}
						onFocus={() =>
							onReferencePreview?.(reference.activityId)
						}
						onBlur={() => onReferencePreview?.(null)}
						onClick={(event) => {
							event.preventDefault();
							onReferenceActivate(reference.activityId);
						}}
						className={cn(
							"mx-0.5 inline-flex cursor-pointer rounded-md px-1.5 py-0.5 align-baseline text-[0.9em] font-medium outline-none transition-[background-color,box-shadow] duration-150 motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
							delegated
								? "bg-violet-500/12 text-violet-700 ring-1 ring-violet-500/20 hover:bg-violet-500/20 dark:text-violet-300"
								: "bg-blue-500/10 text-blue-700 ring-1 ring-blue-500/15 hover:bg-blue-500/20 dark:text-blue-300",
						)}
					>
						{label}
					</a>
				);
			})}
		</div>
	);
}
