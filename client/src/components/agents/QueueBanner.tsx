/**
 * Banner for the FleetPage / agent detail page summarising flagged runs.
 *
 * Shown when there are flagged runs awaiting review. Renders a count,
 * a short description, and a primary action.
 *
 * Optionally dismissible — the parent owns dismiss state so it persists
 * across navigations / view toggles.
 */

import { Sparkles, X } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface QueueBannerProps {
	/** Number of flagged runs in the queue. The banner renders nothing when 0. */
	count: number;
	/** Optional override for the description copy. */
	description?: string;
	/** Optional href for the action button. Rendered as a React Router Link so
	 *  navigation stays in-SPA. */
	actionHref?: string;
	/** Action button label. Defaults to "Review runs". */
	actionLabel?: string;
	/** Click handler. Either this or actionHref should be provided. */
	onAction?: () => void;
	/** When provided, renders a close button that calls this. */
	onDismiss?: () => void;
	className?: string;
}

export function QueueBanner({
	count,
	description,
	actionHref,
	actionLabel = "Review runs",
	onAction,
	onDismiss,
	className,
}: QueueBannerProps) {
	if (count <= 0) return null;
	const subtitle =
		description ??
		"Each flag carries its own diagnosis conversation. Review the evidence before improving the agent.";

	const actionContent = (
		<>
			<Sparkles className="size-4 shrink-0" aria-hidden="true" />{" "}
			{actionLabel}
		</>
	);

	return (
		<div
			className={cn(
				"flex min-w-0 flex-col items-stretch justify-between gap-3 rounded-[var(--bf-radius-surface)] border border-[var(--bf-warning)]/30 bg-[var(--bf-warning-soft)] p-4 sm:flex-row sm:items-center",
				className,
			)}
			data-slot="queue-banner"
			role="status"
		>
			<div className="flex min-w-0 items-start gap-3">
				<span
					className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full bg-[var(--bf-warning)]"
					aria-hidden
				/>
				<div className="min-w-0 [overflow-wrap:anywhere]">
					<div className="text-sm font-medium">
						{count} flagged run{count === 1 ? "" : "s"} to review
					</div>
					<div className="mt-1 text-sm text-muted-foreground">
						{subtitle}
					</div>
				</div>
			</div>
			<div className="flex min-w-0 shrink-0 items-center gap-2 sm:max-w-[45%]">
				{actionHref ? (
					<Button
						size="sm"
						className="min-h-11 h-auto flex-1 whitespace-normal py-2 text-sm sm:flex-none"
						asChild
					>
						<Link to={actionHref}>{actionContent}</Link>
					</Button>
				) : onAction ? (
					<Button
						type="button"
						size="sm"
						className="min-h-11 h-auto flex-1 whitespace-normal py-2 text-sm sm:flex-none"
						onClick={onAction}
					>
						{actionContent}
					</Button>
				) : null}
				{onDismiss ? (
					<Button
						type="button"
						variant="ghost"
						size="icon-sm"
						onClick={onDismiss}
						aria-label="Dismiss"
						className="size-11 shrink-0 text-muted-foreground"
					>
						<X size={14} />
					</Button>
				) : null}
			</div>
		</div>
	);
}
