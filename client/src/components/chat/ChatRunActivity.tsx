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
					"group flex min-h-11 items-center gap-2 rounded-md text-sm text-muted-foreground outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 motion-reduce:transition-none sm:min-h-7",
					hasDetails && "hover:text-foreground",
				)}
			>
				{!isActive && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
				<span className={cn(isActive && "chat-activity-shimmer")}>{label}</span>
				{hasDetails && (
					<ChevronDown
						className={cn(
							"h-3.5 w-3.5 transition-transform duration-200 motion-reduce:transition-none",
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
					<div className="min-h-0 overflow-hidden">
						<div className="mt-1 w-full">{children}</div>
					</div>
				</div>
			)}
		</div>
	);
}
