/**
 * CompactButton (§4.3, §16.5)
 *
 * "Compact older turns" affordance in the chat header. Lossless: clicking it
 * summarizes older turns into a working-context checkpoint server-side; the
 * user's full scrollback is preserved (§4.1).
 *
 * Visibility (suggestion) is budget-driven and reuses the M7 budget math from
 * chat-utils (computeContextUsage + budgetState) — it does NOT duplicate the
 * calculation. The button is rendered only once the budget crosses the
 * "suggest compaction" line (≥70% of the model's window), matching the spec's
 * "suggested visually when the budget indicator approaches 70%."
 */

import { useState } from "react";
import { Layers, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { compactConversation } from "@/services/chatCompaction";
import {
	budgetState,
	computeContextUsage,
	formatCompactTokens,
} from "@/lib/chat-utils";
import type { components } from "@/lib/v1";

type MessagePublic = components["schemas"]["MessagePublic"];

/**
 * Decide whether to surface the compact suggestion given current usage.
 * Exposed for unit testing the threshold logic independent of React.
 */
export function shouldSuggestCompaction(
	used: number,
	contextWindow: number | null,
): boolean {
	if (used === 0) return false;
	const state = budgetState(used, contextWindow);
	if (state.fraction === null) return false;
	return state.fraction >= 0.7;
}

interface CompactButtonProps {
	conversationId: string;
	messages: MessagePublic[];
	contextWindow: number | null;
	/** Called after a successful compaction so the caller can refetch. */
	onCompacted?: () => void;
	className?: string;
}

export function CompactButton({
	conversationId,
	messages,
	contextWindow,
	onCompacted,
	className,
}: CompactButtonProps) {
	const [busy, setBusy] = useState(false);
	const used = computeContextUsage(messages);

	if (!shouldSuggestCompaction(used, contextWindow)) return null;

	const handleClick = async () => {
		if (busy) return;
		setBusy(true);
		try {
			const result = await compactConversation(conversationId);
			if (result.compacted) {
				const freed = Math.max(result.tokens_before - result.tokens_after, 0);
				toast.success("Older turns summarized.", {
					description:
						freed > 0
							? `~${formatCompactTokens(freed)} tokens freed.`
							: result.message,
				});
				onCompacted?.();
			} else {
				toast.info(result.message || "Nothing to compact yet.");
			}
		} catch {
			toast.error("Couldn't compact this conversation. Try again.");
		} finally {
			setBusy(false);
		}
	};

	return (
		<TooltipProvider delayDuration={200}>
			<Tooltip>
				<TooltipTrigger asChild>
					<Button
						variant="ghost"
						size="sm"
						className={className}
						onClick={handleClick}
						disabled={busy}
						aria-label="Compact older turns"
					>
						{busy ? (
							<Loader2 className="h-3.5 w-3.5 animate-spin" />
						) : (
							<Layers className="h-3.5 w-3.5" />
						)}
						<span className="hidden sm:inline text-xs">Compact</span>
					</Button>
				</TooltipTrigger>
				<TooltipContent>
					Summarize older turns to free context space. Your full
					conversation stays visible.
				</TooltipContent>
			</Tooltip>
		</TooltipProvider>
	);
}
