import { useState, type ComponentProps } from "react";
import { Code2, ListTree, Maximize2, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Timeline, AdvancedTimeline } from "@/components/agents/Timeline";
import { RunPayloads } from "@/components/agents/RunReviewPanel";
import { RunDurableEvidence } from "@/components/agents/RunDurableEvidence";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type Run = components["schemas"]["AgentRunDetailResponse"];
export function AgentActivityWorkspace({
	run,
	focused = false,
	expanded = false,
	onFocusedChange,
	onInspectionChange,
	evidenceSequence,
	...timelineProps
}: {
	run: Run;
	focused?: boolean;
	expanded?: boolean;
	onFocusedChange?: (focused: boolean) => void;
	onInspectionChange?: (inspecting: boolean) => void;
	/** Journal sequence deep-link: opens Advanced and highlights the entry. */
	evidenceSequence?: number;
} & Omit<ComponentProps<typeof Timeline>, "steps">) {
	const [advanced, setAdvanced] = useState(
		() => evidenceSequence != null,
	);
	const heading = (
		<div className="flex min-w-0 items-center gap-2">
			<h2 className="flex items-center gap-2 text-sm font-medium">
				<ListTree aria-hidden="true" className="size-4 text-primary" />
				Activity
			</h2>
			<span className="text-xs text-muted-foreground">
				{run.iterations_used} iterations
			</span>
		</div>
	);
	const advancedControl = (
		<div className="ml-auto flex flex-wrap items-center justify-end gap-2">
			{onFocusedChange ? (
				<Button
					variant="ghost"
					aria-pressed={focused}
					onClick={() => onFocusedChange(!focused)}
					className="min-h-11 shrink-0 text-xs"
				>
					{focused ? (
						<Minimize2 className="size-4" />
					) : (
						<Maximize2 className="size-4" />
					)}
					{focused ? "Show overview" : "Focus activity"}
				</Button>
			) : null}
			<Button
				variant={advanced ? "secondary" : "ghost"}
				aria-pressed={advanced}
				onClick={() => {
					onInspectionChange?.(false);
					setAdvanced(!advanced);
				}}
				className="min-h-11 shrink-0 text-xs"
			>
				<Code2 className="size-4" />
				Advanced
			</Button>
		</div>
	);
	return (
		<section
			data-slot="run-activity"
			aria-label="Agent activity workspace"
			className={cn(
				"flex min-w-0 flex-col lg:min-h-0",
				(focused || expanded) && "lg:min-h-0 lg:flex-1",
				advanced && "gap-3",
			)}
		>
			{advanced ? (
				<div
					className="min-w-0 lg:min-h-0 lg:flex-1 lg:overflow-auto"
					role="region"
					aria-label="Advanced activity"
				>
					<div
						data-slot="activity-advanced-content"
						className="space-y-5 pb-5"
					>
						<div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-4 py-3">
							{heading}
							{advancedControl}
						</div>
						<RunPayloads input={run.input} output={run.output} />
						<h3 className="text-sm font-semibold">
							Raw executor trace
						</h3>
						<AdvancedTimeline steps={run.steps ?? []} />
						<RunDurableEvidence
							runId={run.id}
							agentId={run.agent_id ?? ""}
							highlightSequence={evidenceSequence}
						/>
					</div>
				</div>
			) : (
				<Timeline
					{...timelineProps}
					steps={run.steps ?? []}
					inspector="inline"
					joinedRows
					toolbarLeading={heading}
					toolbarActions={advancedControl}
					onInspectionChange={onInspectionChange}
				/>
			)}
		</section>
	);
}
