/**
 * DelegationBadge Component (M6 — multi-agent delegation in chat)
 *
 * Renders a "✓ consulted <agent>" badge for a delegate_to_* tool call. While
 * the delegated agent is running it shows a spinner ("consulting <agent>");
 * once the delegation completes it flips to a check (or an error icon) and
 * exposes the delegated exchange — task + response — in an expandable popover.
 */

import { useState } from "react";
import { CheckCircle2, XCircle, Loader2, ChevronDown, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import type { ChatDelegationInfo } from "@/services/websocket";

interface DelegationBadgeProps {
	delegation: ChatDelegationInfo;
	/** Tool state from the underlying tool_call message. */
	status: "running" | "completed" | "error";
	durationMs?: number;
	className?: string;
}

const formatDuration = (ms: number) => {
	if (ms < 1000) return `${ms}ms`;
	return `${(ms / 1000).toFixed(1)}s`;
};

export function DelegationBadge({
	delegation,
	status,
	durationMs,
	className,
}: DelegationBadgeProps) {
	const [isOpen, setIsOpen] = useState(false);

	const isRunning = status === "running" && !delegation.response && !delegation.error;
	const isError = status === "error" || Boolean(delegation.error);

	// Use design tokens, not raw palette colors: in-progress reads as the
	// accent (primary), a settled "consulted" badge is de-emphasized
	// (muted-foreground), errors use the destructive token.
	const Icon = isRunning ? Loader2 : isError ? XCircle : CheckCircle2;
	const iconClass = isRunning
		? "text-primary animate-spin"
		: isError
			? "text-destructive"
			: "text-muted-foreground";
	const badgeClass = isRunning
		? "bg-primary/10 text-primary hover:bg-primary/20 border-primary/30 animate-pulse"
		: isError
			? "bg-destructive/10 text-destructive hover:bg-destructive/20 border-destructive/30"
			: "bg-muted text-muted-foreground hover:bg-muted/80 border-border";

	const label = isRunning
		? `consulting ${delegation.agent_name}`
		: `consulted ${delegation.agent_name}`;

	const shownDuration = durationMs ?? delegation.duration_ms ?? undefined;

	const hasDetail =
		Boolean(delegation.task) ||
		Boolean(delegation.response) ||
		Boolean(delegation.error);

	return (
		<Popover open={isOpen} onOpenChange={setIsOpen}>
			<PopoverTrigger asChild>
				<Badge
					variant="outline"
					data-testid="delegation-badge"
					className={cn(
						"cursor-pointer gap-1.5 px-2 py-1 text-xs font-normal transition-colors",
						badgeClass,
						className,
					)}
				>
					<Icon className={cn("h-3 w-3", iconClass)} />
					<Users className="h-3 w-3 opacity-70" />
					<span className="font-medium">{label}</span>
					{shownDuration !== undefined && !isRunning && (
						<span className="text-muted-foreground">
							{formatDuration(shownDuration)}
						</span>
					)}
					{hasDetail && (
						<ChevronDown
							className={cn(
								"h-3 w-3 text-muted-foreground transition-transform",
								isOpen && "rotate-180",
							)}
						/>
					)}
				</Badge>
			</PopoverTrigger>

			{hasDetail && (
				<PopoverContent
					className="w-96 max-h-80 overflow-auto"
					align="start"
				>
					<div className="space-y-3">
						{delegation.task && (
							<div>
								<h4 className="text-xs font-medium text-muted-foreground mb-1">
									Delegated task
								</h4>
								<p className="text-xs whitespace-pre-wrap">
									{delegation.task}
								</p>
							</div>
						)}

						{delegation.error && (
							<div>
								<h4 className="text-xs font-medium text-destructive mb-1">
									Error
								</h4>
								<pre className="text-xs font-mono text-destructive whitespace-pre-wrap">
									{delegation.error}
								</pre>
							</div>
						)}

						{delegation.response && !delegation.error && (
							<div>
								<h4 className="text-xs font-medium text-muted-foreground mb-1">
									{delegation.agent_name}'s response
								</h4>
								<p className="text-xs whitespace-pre-wrap">
									{delegation.response}
								</p>
							</div>
						)}
					</div>
				</PopoverContent>
			)}
		</Popover>
	);
}
