import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export function formatRunDuration(durationMs?: number | null): string {
	if (!durationMs || durationMs < 1000) return "less than a second";
	const totalSeconds = Math.round(durationMs / 1000);
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	if (minutes === 0) return `${seconds}s`;
	return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

export function getActiveRunLabel(
	toolName?: string | null,
	toolInput?: Record<string, unknown> | null,
): string {
	if (!toolName) return "Thinking…";
	if (toolName.startsWith("create_") && toolName.endsWith("_artifact")) {
		if (toolName === "create_image_artifact") return "Generating image…";
		if (toolName === "create_video_artifact") {
			return "Starting video generation…";
		}
		const rawFormat = String(toolInput?.format ?? "").toLowerCase();
		const filename = String(toolInput?.filename ?? "");
		const extension = filename.includes(".")
			? filename.split(".").pop()?.toLowerCase()
			: "";
		const format = rawFormat || extension || "file";
		const formatLabels: Record<string, string> = {
			html: "HTML",
			pdf: "PDF",
			docx: "DOCX",
			xlsx: "XLSX",
			csv: "CSV",
			json: "JSON",
			markdown: "Markdown",
			md: "Markdown",
			text: "text file",
			txt: "text file",
		};
		return `Generating ${formatLabels[format] || format}…`;
	}
	const friendlyName = toolName.replaceAll("_", " ");
	return `Running ${friendlyName}…`;
}

export function ChatRunActivity({
	isActive,
	durationMs,
	activeLabel = "Thinking…",
	children,
}: {
	isActive: boolean;
	durationMs?: number | null;
	activeLabel?: string;
	children?: React.ReactNode;
}) {
	const [isExpanded, setIsExpanded] = useState(false);
	const hasDetails = Boolean(children);
	const label = isActive
		? activeLabel
		: `Worked for ${formatRunDuration(durationMs)}`;

	return (
		<div className="px-4 py-2" aria-live={isActive ? "polite" : undefined}>
			<button
				type="button"
				disabled={!hasDetails}
				onClick={() => setIsExpanded((value) => !value)}
				aria-expanded={hasDetails ? isExpanded : undefined}
				className={cn(
					"group flex min-h-11 w-full items-start justify-between gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 bg-muted/30 px-3 py-3 text-left text-sm leading-6 text-muted-foreground outline-none transition-colors duration-150 motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
					hasDetails && "hover:border-border hover:text-foreground",
				)}
			>
				{!isActive && (
					<Check className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
				)}
				<span className={cn("min-w-0 flex-1 break-words", isActive && "chat-activity-shimmer")}>
					{label}
				</span>
				{hasDetails && (
					<ChevronDown
						className={cn(
							"mt-0.5 h-4 w-4 shrink-0 transition-transform duration-200 motion-reduce:transition-none",
							isExpanded && "rotate-180",
						)}
						aria-hidden="true"
					/>
				)}
			</button>
			{hasDetails && (
				<div
					aria-hidden={!isExpanded}
					inert={!isExpanded ? true : undefined}
					className={cn(
						"grid w-full transition-[grid-template-rows,opacity] motion-reduce:transition-none",
						isExpanded
							? "grid-rows-[1fr] opacity-100 duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
							: "pointer-events-none grid-rows-[0fr] opacity-0 duration-200 ease-out",
					)}
				>
					{/* Keep focus/scrollIntoView from scrolling the clipped content
					 * while the grid expands and moving a tool under the pointer. */}
					<div className="min-h-0 overflow-clip">
						<div className="mt-2 w-full rounded-[var(--bf-radius-surface)] border border-border/70 bg-background/70 p-3 text-sm leading-6 text-foreground">
							{children}
						</div>
					</div>
				</div>
			)}
		</div>
	);
}
