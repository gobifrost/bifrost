import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { Sparkles } from "lucide-react";

import { cn } from "@/lib/utils";

export type ChatBubbleKind = "user" | "assistant" | "system";

export interface ChatBubbleProps {
	kind: ChatBubbleKind;
	children: ReactNode;
	/** Optional timestamp shown in 10.5px subtle tone under the bubble. */
	time?: ReactNode;
	/**
	 * Nested tool-result blocks (proposals, dry-run previews, etc.) rendered
	 * below the prose, *inside* the same bubble. Assistant bubbles only.
	 */
	slots?: ReactNode;
	className?: string;
}

/**
 * Message bubble for per-flag chat and improvement conversations.
 *
 * Layout:
 *   user      → right-aligned, primary-tinted background
 *   assistant → left-aligned with sparkles avatar, muted-2 bg + border
 *   system    → centered, muted, no avatar
 *
 * When `slots` is passed on an assistant bubble, each slot renders as a nested
 * block inside the same bubble below the prose — styled as a tool-result card
 * so it's visually inside the assistant's turn, not a floating sibling card.
 * This is the pattern used for ProposalTurn + DryRunTurn in improvement chat.
 */
export function ChatBubble({
	kind,
	children,
	time,
	slots,
	className,
	...props
}: ChatBubbleProps & ComponentPropsWithoutRef<"div">) {
	if (kind === "user") {
		return (
			<div
				className={cn("flex flex-col items-end", className)}
				{...props}
			>
				<div className="min-w-0 max-w-[92%] rounded-[var(--bf-radius-surface)] border border-border/70 bg-card px-3.5 py-3 text-[13.5px] leading-6 text-foreground whitespace-pre-wrap [overflow-wrap:anywhere]">
					{children}
				</div>
				{time ? (
					<div className="mt-1 text-[10.5px] leading-4 text-muted-foreground">
						{time}
					</div>
				) : null}
			</div>
		);
	}

	if (kind === "system") {
		return (
			<div className={cn("flex justify-center", className)} {...props}>
				<div className="max-w-[min(100%,40rem)] rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/30 px-3 py-2 text-[12px] leading-5 text-muted-foreground whitespace-pre-wrap [overflow-wrap:anywhere]">
					{children}
				</div>
			</div>
		);
	}

	// assistant
	return (
		<div className={cn("flex items-start gap-2", className)} {...props}>
			<div
				aria-hidden
				className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/40 text-primary"
			>
				<Sparkles className="h-3.5 w-3.5" />
			</div>
			<div className="min-w-0 flex-1 rounded-[var(--bf-radius-surface)] border border-border/70 bg-card px-3.5 py-3 text-[13.5px] leading-6 text-foreground shadow-sm">
				<div className="whitespace-pre-wrap [overflow-wrap:anywhere]">
					{children}
				</div>
				{slots ? (
					<div className="mt-3 min-w-0 space-y-2">{slots}</div>
				) : null}
				{time ? (
					<div className="mt-1 text-[10.5px] leading-4 text-muted-foreground">
						{time}
					</div>
				) : null}
			</div>
		</div>
	);
}

// ──────────────────────────────────────────────────────────────────────────
// Slot helpers — nested tool-result blocks rendered inside assistant bubbles
// ──────────────────────────────────────────────────────────────────────────

export interface ChatBubbleSlotProps {
	title: ReactNode;
	titleTone?: "primary" | "emerald" | "yellow";
	actions?: ReactNode;
	children: ReactNode;
}

/**
 * Nested block inside a ChatBubble slot. Recessed step-1 block with a small
 * colored title line and an optional action row at the bottom.
 */
export function ChatBubbleSlot({
	title,
	titleTone = "primary",
	actions,
	children,
}: ChatBubbleSlotProps) {
	const titleColor =
		titleTone === "emerald"
			? "text-[var(--bf-success)]"
			: titleTone === "yellow"
				? "text-[var(--bf-warning)]"
				: "text-primary";
	return (
		<div className="rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/20 p-3">
			<div
				className={cn(
					"mb-2 text-[12px] font-medium leading-5",
					titleColor,
				)}
			>
				{title}
			</div>
			<div className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]">
				{children}
			</div>
			{actions ? (
				<div className="mt-3 flex flex-wrap items-center gap-2">
					{actions}
				</div>
			) : null}
		</div>
	);
}
