import { AGENT_PROMPT_MAX_LENGTH } from "@/lib/agent-prompt";
import { useId, useState, type ReactNode } from "react";
import { Check, Loader2, X, Columns2, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PromptDiffViewer } from "@/components/agents/PromptDiffViewer";

/** Review and editing share the full workspace; supporting evidence stays outside it. */
export function TuningProposalEditor({
	currentPrompt,
	edits,
	busy,
	applying,
	actions,
	onChange,
	onDiscard,
	onApply,
}: {
	currentPrompt: string;
	edits: string;
	busy: boolean;
	applying: boolean;
	actions?: ReactNode;
	onChange: (value: string) => void;
	onDiscard: () => void;
	onApply: () => void;
}) {
	const inputId = useId();
	const [view, setView] = useState("diff");
	return (
		<div
			className="flex min-w-0 flex-col gap-3 lg:min-h-0 lg:flex-1"
			data-testid="tune-pane-editor"
		>
			<Tabs
				value={view}
				onValueChange={setView}
				className="min-w-0 gap-0 overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card lg:min-h-0 lg:flex-1"
			>
				<div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
					<TabsList
						aria-label="Prompt Workspace"
						className="h-auto! min-h-12"
					>
						<TabsTrigger value="diff" className="min-h-11 px-4">
							<Columns2 aria-hidden="true" />
							Diff
						</TabsTrigger>
						<TabsTrigger value="edit" className="min-h-11 px-4">
							<Pencil aria-hidden="true" />
							Edit
						</TabsTrigger>
					</TabsList>
					<p className="text-xs text-muted-foreground">
						{edits.length.toLocaleString()} /{" "}
						{AGENT_PROMPT_MAX_LENGTH.toLocaleString()} characters
					</p>
				</div>
				<TabsContent
					value="diff"
					forceMount
					hidden={view !== "diff"}
					className="m-0 min-w-0 max-h-[65svh] overflow-auto data-[state=inactive]:hidden lg:min-h-0 lg:max-h-none lg:flex-1"
					aria-label="Prompt Diff"
				>
					<div className="flex justify-between gap-4 border-b bg-muted/20 px-4 py-2 text-xs font-medium text-muted-foreground">
						<span>Current Prompt</span>
						<span>Proposed Prompt</span>
					</div>
					<PromptDiffViewer
						before={currentPrompt}
						after={edits}
						className="rounded-none border-0"
					/>
				</TabsContent>
				<TabsContent
					value="edit"
					forceMount
					hidden={view !== "edit"}
					className="m-0 min-w-0 data-[state=inactive]:hidden lg:min-h-0 lg:flex-1"
					aria-label="Edit Prompt"
				>
					<label htmlFor={inputId} className="sr-only">
						Proposed prompt (editable)
					</label>
					<Textarea
						id={inputId}
						data-testid="proposal-textarea"
						value={edits}
						disabled={busy}
						onChange={(event) => onChange(event.target.value)}
						rows={16}
						maxLength={AGENT_PROMPT_MAX_LENGTH}
						className="h-[55svh] max-h-[65svh] resize-none overflow-auto rounded-none border-0 p-4 font-mono text-base sm:text-sm [field-sizing:fixed] focus-visible:ring-inset lg:h-full lg:max-h-none"
					/>
				</TabsContent>
			</Tabs>
			<div className="shrink-0 space-y-3">
				<p className="text-xs text-muted-foreground">
					Applying updates the live prompt and clears the reviewed
					flags.
				</p>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="flex flex-wrap gap-2">{actions}</div>
					<div className="flex flex-wrap gap-2">
						<Button
							variant="outline"
							data-testid="discard-button"
							className="min-h-11"
							disabled={busy}
							onClick={onDiscard}
						>
							<X className="size-4" aria-hidden="true" />
							Discard
						</Button>
						<Button
							data-testid="apply-button"
							className="min-h-11"
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
							Apply Live
						</Button>
					</div>
				</div>
			</div>
		</div>
	);
}
