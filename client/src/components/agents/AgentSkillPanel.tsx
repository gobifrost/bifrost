import { useMutation, useQuery } from "@tanstack/react-query";
import {
	BookOpen,
	Download,
	FileCode2,
	FolderTree,
	Loader2,
	Sparkles,
} from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import {
	CARD_BODY,
	CARD_HEADER,
	CARD_SURFACE,
	TONE_MUTED,
	TYPE_CARD_TITLE,
	TYPE_MONO,
	TYPE_SMALL,
} from "@/components/agents/design-tokens";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
	downloadAgentSkill,
	getAgentSkill,
	type AgentSkillDownload,
} from "@/services/agentSkills";
import { cn } from "@/lib/utils";

function triggerDownload({ blob, filename }: AgentSkillDownload) {
	const url = URL.createObjectURL(blob);
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	anchor.click();
	URL.revokeObjectURL(url);
}

export function AgentSkillPanel({ agentId }: { agentId: string }) {
	const skillQuery = useQuery({
		queryKey: ["agent-skill", agentId],
		queryFn: ({ signal }) => getAgentSkill(agentId, { signal }),
	});
	const downloadMutation = useMutation({
		mutationFn: () => downloadAgentSkill(agentId),
		onSuccess: triggerDownload,
		onError: (error: Error) => toast.error(error.message),
	});

	if (skillQuery.isLoading) {
		return <Skeleton className="h-44 w-full" />;
	}
	if (skillQuery.isError || !skillQuery.data) {
		return (
			<section className={cn(CARD_SURFACE, CARD_BODY, "space-y-2")}>
				<p className={TYPE_CARD_TITLE}>Agent Skill unavailable</p>
				<p className={cn(TYPE_SMALL, TONE_MUTED)}>
					{skillQuery.error instanceof Error
						? skillQuery.error.message
						: "Skill details could not be loaded."}
				</p>
				<Button
					variant="outline"
					size="sm"
					onClick={() => skillQuery.refetch()}
				>
					Try again
				</Button>
			</section>
		);
	}

	const skill = skillQuery.data;

	return (
		<section className={cn(CARD_SURFACE, "overflow-hidden")}>
			<div className={cn("flex items-center justify-between gap-3", CARD_HEADER)}>
				<div className={cn("flex items-center gap-2", TYPE_CARD_TITLE)}>
					<Sparkles className="h-3.5 w-3.5 text-primary" />
					Agent Skill
				</div>
				<span className={cn(TYPE_SMALL, TONE_MUTED)}>
					{skill.source === "solution"
						? "Solution managed"
						: skill.source === "upload"
							? "Uploaded"
							: "Portable"}
				</span>
			</div>
			<div className={cn("space-y-3", CARD_BODY)}>
				<div>
					<div className="font-medium">{skill.name}</div>
					<p className={cn("mt-0.5", TYPE_SMALL, TONE_MUTED)}>
						{skill.bundle_path
							? "SKILL.md is the instruction source; runtime bindings stay separate."
							: "Inline instructions export as SKILL.md; runtime bindings stay separate."}
					</p>
				</div>

				<div className="space-y-1.5 border-y py-3">
					<div className="flex items-center gap-2 text-[13px]">
						<BookOpen className="h-3.5 w-3.5 text-muted-foreground" />
						<span>SKILL.md</span>
						<span className={cn("ml-auto", TONE_MUTED)}>instructions</span>
					</div>
					{skill.bundle_path ? (
						<div className="flex min-w-0 items-center gap-2 text-[13px]">
							<FileCode2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
							<code className={cn("truncate", TYPE_MONO)}>
								{skill.bundle_path}
							</code>
						</div>
					) : (
						<p className={cn(TYPE_SMALL, TONE_MUTED)}>
							Inline-only skill · no companion bundle configured
						</p>
					)}
				</div>

				{skill.automatic_capabilities.includes("read_skill_asset") ? (
					<p className="rounded-md bg-primary/10 px-2.5 py-2 text-[12.5px] text-foreground ring-1 ring-primary/15">
						Runs can read referenced bundle files through{" "}
						<code className={TYPE_MONO}>read_skill_asset</code>.
					</p>
				) : null}

				{skill.companion_files.length > 0 ? (
					<div className="max-h-32 space-y-1 overflow-auto pr-1">
						{skill.companion_files.map((path) => (
							<div
								key={path}
								className={cn("truncate text-[12px]", TYPE_MONO, TONE_MUTED)}
								title={path}
							>
								{path}
							</div>
						))}
					</div>
				) : null}

				{skill.bundle_path ? (
					<Button asChild variant="outline" size="sm" className="w-full">
						<Link to="?tab=settings">
							<FolderTree className="h-3.5 w-3.5" />
							Browse bundle
						</Link>
					</Button>
				) : null}

				<Button
					variant="secondary"
					size="sm"
					className="w-full"
					disabled={downloadMutation.isPending}
					onClick={() => downloadMutation.mutate()}
				>
					{downloadMutation.isPending ? (
						<Loader2 className="h-3.5 w-3.5 animate-spin" />
					) : (
						<Download className="h-3.5 w-3.5" />
					)}
					Download skill
				</Button>
			</div>
		</section>
	);
}
