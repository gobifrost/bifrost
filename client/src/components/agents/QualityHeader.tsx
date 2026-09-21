import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { ListPageHeader } from "@/components/layout/ListPageHeader";

export interface QualityHeaderProps {
	agentId: string | undefined;
	agentName: string | undefined;
}

/**
 * Workbench header — one ListPageHeader-based shell for the whole quality
 * workspace. Production statistics live on the agent overview.
 */
export function QualityHeader({ agentId, agentName }: QualityHeaderProps) {
	return (
		<div className="space-y-3">
			<Link
				to={agentId ? `/agents/${agentId}` : "/agents"}
				className="inline-flex min-h-11 w-fit max-w-full items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
			>
				<ArrowLeft className="size-4 shrink-0" aria-hidden="true" />
				<span className="min-w-0 [overflow-wrap:anywhere]">
					{agentName ?? "Back to agent"}
				</span>
			</Link>
			<ListPageHeader
				title="Quality workbench"
				description="Review runs, turn findings into tests, compare changes, and inspect results."
			/>
		</div>
	);
}
