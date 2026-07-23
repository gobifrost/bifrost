/**
 * Compact card for one agent run, used in the agent detail Runs tab.
 *
 * Shows: status indicator, asked text, did/error text, verdict badge,
 * timing metadata (when, duration, tokens), and inline verdict toggles.
 *
 * Adapted from the mockup's `RunCard` (AgentDetailPage.tsx) — replaces inline
 * styles with Tailwind + shadcn primitives.
 */

import { ThumbsDown, ThumbsUp } from "lucide-react";
import type { MouseEvent } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

import type { Verdict } from "./RunReviewPanel";
import { RunSummaryContent } from "./RunSummaryContent";

type AgentRun = components["schemas"]["AgentRunResponse"];

export interface RunCardProps {
	run: AgentRun;
	verdict?: Verdict;
	highlight?: string;
	onOpen?: () => void;
	onVerdict?: (v: Verdict) => void;
	/** Called when the inline "what should it have done" note is saved.
	 *  Only surfaces while verdict === "down" and this callback is provided. */
	onNote?: (runId: string, note: string) => void;
	conversationCount?: number;
}

export function RunCard({
	run,
	verdict = null,
	highlight,
	onOpen,
	onVerdict,
	onNote,
	conversationCount = 0,
}: RunCardProps) {
	const canVerdict = run.status === "completed";

	function handleVerdict(target: Verdict, e: MouseEvent) {
		e.stopPropagation();
		if (!onVerdict) return;
		onVerdict(verdict === target ? null : target);
	}

	const showNoteInput = verdict === "down" && onNote;
	return (
		<div
			className={cn(
				"rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 transition-colors dark:ring-foreground/10",
				onOpen && "hover:bg-accent/50",
			)}
			data-slot="run-card"
		>
			<div
				role={onOpen ? "button" : undefined}
				tabIndex={onOpen ? 0 : undefined}
				onClick={onOpen}
				onKeyDown={(e) => {
					if (onOpen && (e.key === "Enter" || e.key === " ")) {
						e.preventDefault();
						onOpen();
					}
				}}
				className={cn(
					"flex items-start gap-3 p-3",
					onOpen && "cursor-pointer",
				)}
			>
				<RunSummaryContent
					run={run}
					highlight={highlight}
					titleTrailing={
						verdict === "up" ? (
							<span className="inline-flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
								<ThumbsUp size={11} /> Good
							</span>
						) : verdict === "down" ? (
							<span className="inline-flex items-center gap-1 rounded border border-rose-500/30 bg-rose-500/10 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 dark:text-rose-300">
								<ThumbsDown size={11} /> Wrong
								{conversationCount > 0
									? ` · ${conversationCount} msg`
									: ""}
							</span>
						) : null
					}
				/>

				<div
					className="flex shrink-0 items-center"
					onClick={(e) => e.stopPropagation()}
				>
					{canVerdict && onVerdict ? (
						<div className="flex gap-1">
							<button
								type="button"
								aria-label="Mark as good"
								aria-pressed={verdict === "up"}
								title="Good"
								onClick={(e) => handleVerdict("up", e)}
								className={cn(
									"grid h-7 w-7 place-items-center rounded-full border transition-colors",
									verdict === "up"
										? "border-emerald-500 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
										: "bg-background hover:bg-accent",
								)}
							>
								<ThumbsUp size={14} />
							</button>
							<button
								type="button"
								aria-label="Mark as wrong"
								aria-pressed={verdict === "down"}
								title="Wrong"
								onClick={(e) => handleVerdict("down", e)}
								className={cn(
									"grid h-7 w-7 place-items-center rounded-full border transition-colors",
									verdict === "down"
										? "border-rose-500 bg-rose-500/15 text-rose-600 dark:text-rose-400"
										: "bg-background hover:bg-accent",
								)}
							>
								<ThumbsDown size={14} />
							</button>
						</div>
					) : (
						<span className="text-[11px] text-muted-foreground">
							n/a
						</span>
					)}
				</div>
			</div>
			{showNoteInput ? (
				<div className="border-t px-3 py-2">
					<Input
						type="text"
						aria-label="What should it have done?"
						placeholder="What should it have done?"
						defaultValue={run.verdict_note ?? ""}
						onClick={(e) => e.stopPropagation()}
						onKeyDown={(e) => {
							e.stopPropagation();
							if (e.key === "Enter") {
								(e.currentTarget as HTMLInputElement).blur();
							}
						}}
						onBlur={(e) => {
							const next = e.currentTarget.value.trim();
							const prev = run.verdict_note ?? "";
							if (next !== prev) onNote?.(run.id, next);
						}}
						className="h-7 text-xs"
						data-testid="run-card-note-input"
					/>
				</div>
			) : null}
		</div>
	);
}
