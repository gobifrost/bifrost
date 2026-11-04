import { AGENT_PROMPT_MAX_LENGTH } from "@/lib/agent-prompt";
import { TuningActionError } from "./TuningActionError";
import { useId } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PromptDiffViewer } from "@/components/agents/PromptDiffViewer";

export function TuningProposalEditor({
	currentPrompt,
	edits,
	summary,
	error,
	busy,
	applying,
	onChange,
	onDiscard,
	onApply,
}: {
	currentPrompt: string;
	edits: string;
	summary?: string | null;
	error?: string;
	busy: boolean;
	applying: boolean;
	onChange: (value: string) => void;
	onDiscard: () => void;
	onApply: () => void;
}) {
	const inputId = useId();
	return (
		<div className="flex min-w-0 flex-col gap-4">
			<div className="overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card focus-within:ring-2 focus-within:ring-ring">
				<label
					htmlFor={inputId}
					className="block border-b px-4 py-3 text-sm font-medium"
				>
					Proposed prompt (editable)
				</label>
				<Textarea
					id={inputId}
					data-testid="proposal-textarea"
					value={edits}
					disabled={busy}
					onChange={(event) => onChange(event.target.value)}
					rows={12}
					maxLength={AGENT_PROMPT_MAX_LENGTH}
					className="min-h-72 max-h-96 overflow-y-auto resize-y rounded-none border-0 p-4 font-mono text-base sm:text-sm [field-sizing:fixed] focus-visible:ring-0"
				/>
				<p className="border-t px-4 py-2 text-xs text-muted-foreground">
					{edits.length.toLocaleString()} /{" "}
					{AGENT_PROMPT_MAX_LENGTH.toLocaleString()} characters
				</p>
			</div>
			{summary && (
				<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
					{summary}
				</p>
			)}
			<PromptDiffViewer before={currentPrompt} after={edits} />
			{error && <TuningActionError message={error} />}
			<p className="text-sm text-muted-foreground">
				Applying updates the live prompt and clears the reviewed flags.
			</p>
			<div className="flex flex-wrap items-center justify-end gap-2 [&>button]:min-h-11 [&>button]:flex-1 sm:[&>button]:flex-none">
				<Button
					variant="outline"
					data-testid="discard-button"
					disabled={busy}
					onClick={onDiscard}
				>
					<X className="size-4" aria-hidden="true" />
					Discard
				</Button>
				<Button
					data-testid="apply-button"
					disabled={busy || !edits.trim()}
					onClick={onApply}
				>
					{applying ? (
						<Loader2
							className="size-4 animate-spin motion-reduce:animate-none"
							aria-hidden="true"
						/>
					) : (
						<Check className="size-4" aria-hidden="true" />
					)}
					Apply live
				</Button>
			</div>
		</div>
	);
}
