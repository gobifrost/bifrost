/**
 * Match Suggestion Actions
 * Confidence score plus accept/reject for one auto-match suggestion. The
 * suggested entity itself is shown in the row's entity dropdown.
 */

import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MatchSuggestion } from "@/lib/matching";
import { cn } from "@/lib/utils";

export function isHighConfidence(suggestion: MatchSuggestion): boolean {
	return suggestion.score >= 80;
}

interface MatchSuggestionActionsProps {
	suggestion: MatchSuggestion;
	onAccept: () => void;
	onReject: () => void;
	disabled?: boolean;
}

export function MatchSuggestionActions({
	suggestion,
	onAccept,
	onReject,
	disabled = false,
}: MatchSuggestionActionsProps) {
	return (
		<div className="flex items-center gap-1">
			<span
				className={cn(
					"w-11 text-xs font-medium tabular-nums",
					isHighConfidence(suggestion)
						? "text-[var(--bf-success)]"
						: "text-[var(--bf-warning)]",
				)}
				title="Match confidence"
			>
				{suggestion.score}%
			</span>
			<Button
				size="icon"
				variant="ghost"
				className="size-9 text-[var(--bf-success)] hover:bg-[var(--bf-success-soft)] hover:text-[var(--bf-success)]"
				onClick={onAccept}
				disabled={disabled}
				aria-label={`Accept suggestion ${suggestion.entityName}`}
				title="Accept"
			>
				<Check className="size-4" />
			</Button>
			<Button
				size="icon"
				variant="ghost"
				className="size-9 text-destructive hover:bg-destructive/10 hover:text-destructive"
				onClick={onReject}
				disabled={disabled}
				aria-label={`Reject suggestion ${suggestion.entityName}`}
				title="Reject"
			>
				<X className="size-4" />
			</Button>
		</div>
	);
}
