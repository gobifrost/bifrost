import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileDiff, GitCompareArrows } from "lucide-react";

import { RevisionList } from "@/components/builder/RevisionList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
	getRevisionDiff,
	type BuilderRevision,
	type BuilderRevisionDiffFile,
} from "@/services/builder";
import { cn } from "@/lib/utils";

function statusTone(status: BuilderRevisionDiffFile["status"]) {
	if (status === "added") return "text-emerald-600 dark:text-emerald-400";
	if (status === "deleted") return "text-rose-600 dark:text-rose-400";
	return "text-amber-600 dark:text-amber-400";
}

function DiffContent({ file }: { file: BuilderRevisionDiffFile }) {
	if (file.is_binary) {
		return (
			<div className="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">
				Binary file changed. Inspect it from the downloaded source archive.
			</div>
		);
	}
	const lines = (file.diff ?? "").split("\n");
	return (
		<pre className="min-h-full min-w-max py-3 font-mono text-[12px] leading-5">
			{lines.map((line, index) => (
				<div
					// A diff can contain duplicate lines; the line number is part of
					// its stable position in this immutable rendering.
					key={`${index}-${line}`}
					className={cn(
						"px-4",
						line.startsWith("+") &&
							!line.startsWith("+++") &&
							"bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
						line.startsWith("-") &&
							!line.startsWith("---") &&
							"bg-rose-500/10 text-rose-800 dark:text-rose-200",
						line.startsWith("@@") && "bg-primary/10 text-primary",
					)}
				>
					{line || " "}
				</div>
			))}
			{file.truncated ? (
				<div className="px-4 py-2 text-amber-600 dark:text-amber-400">
					Diff preview truncated
				</div>
			) : null}
		</pre>
	);
}

export function BuilderChangesPanel({
	solutionId,
	revisions,
	isLoading,
	canUndo,
	undoingRevisionId,
	onUndo,
	onDownload,
}: {
	solutionId: string;
	revisions: BuilderRevision[];
	isLoading: boolean;
	canUndo: boolean;
	undoingRevisionId: string | null;
	onUndo: (revisionId: string) => void;
	onDownload: (revisionId: string) => void;
}) {
	const source = revisions.find((revision) => revision.is_current) ?? null;
	const [selectedRevisionId, setSelectedRevisionId] = useState<string | null>(
		null,
	);
	const selectedRevision =
		revisions.find((revision) => revision.id === selectedRevisionId) ??
		source ??
		revisions[0] ??
		null;
	const diffQuery = useQuery({
		queryKey: [
			"builder",
			"revision-diff",
			solutionId,
			selectedRevision?.id,
		],
		queryFn: ({ signal }) =>
			getRevisionDiff(solutionId, selectedRevision!.id, null, { signal }),
		enabled: Boolean(selectedRevision),
	});
	const diffFiles = useMemo(() => diffQuery.data?.files ?? [], [diffQuery.data]);
	const [selectedPath, setSelectedPath] = useState<string | null>(null);
	const activeFile =
		diffFiles.find((file) => file.path === selectedPath) ??
		diffFiles[0] ??
		null;

	return (
		<div className="flex h-full min-h-0 flex-col bg-background lg:flex-row">
			<aside className="max-h-56 shrink-0 overflow-auto border-b lg:max-h-none lg:w-[290px] lg:border-b-0 lg:border-r">
				<div className="sticky top-0 z-10 flex items-center gap-2 border-b bg-background px-3 py-2 text-xs font-medium">
					<GitCompareArrows className="h-3.5 w-3.5" />
					Revision history
				</div>
				<RevisionList
					revisions={revisions}
					isLoading={isLoading}
					canUndo={canUndo}
					undoingRevisionId={undoingRevisionId}
					onUndo={onUndo}
					onDownload={onDownload}
					selectedRevisionId={selectedRevision?.id ?? null}
					onSelect={setSelectedRevisionId}
				/>
			</aside>

			<section className="flex min-h-0 min-w-0 flex-1 flex-col">
				<div className="flex min-h-11 flex-wrap items-center gap-2 border-b px-3 py-2">
					<FileDiff className="h-4 w-4 text-muted-foreground" />
					<span className="text-sm font-medium">
						{selectedRevision?.summary ?? "Revision changes"}
					</span>
					{diffQuery.data ? (
						<>
							<Badge variant="outline">{diffQuery.data.total} files</Badge>
							<span className="text-xs text-emerald-600 dark:text-emerald-400">
								+{diffQuery.data.additions}
							</span>
							<span className="text-xs text-rose-600 dark:text-rose-400">
								−{diffQuery.data.deletions}
							</span>
						</>
					) : null}
					<span className="ml-auto text-xs text-muted-foreground">
						{diffQuery.data?.against_revision_id
							? `vs ${diffQuery.data.against_revision_id.slice(0, 8)}`
							: "initial source"}
					</span>
				</div>

				{diffQuery.isLoading ? (
					<div className="space-y-2 p-4">
						<Skeleton className="h-8 w-full" />
						<Skeleton className="h-48 w-full" />
					</div>
				) : diffQuery.isError ? (
					<div className="space-y-2 p-4">
						<p className="text-sm text-destructive">
							{(diffQuery.error as Error).message}
						</p>
						<Button
							variant="outline"
							size="sm"
							onClick={() => diffQuery.refetch()}
						>
							Try again
						</Button>
					</div>
				) : diffFiles.length === 0 ? (
					<div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
						This revision has no file changes from its parent.
					</div>
				) : (
					<>
						<div className="flex shrink-0 gap-1 overflow-x-auto border-b p-2">
							{diffFiles.map((file) => (
								<button
									type="button"
									key={file.path}
									className={cn(
										"flex shrink-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-xs hover:bg-accent",
										activeFile?.path === file.path && "bg-accent",
									)}
									onClick={() => setSelectedPath(file.path)}
								>
									<span className={cn("font-semibold", statusTone(file.status))}>
										{file.status.charAt(0).toUpperCase()}
									</span>
									<span>{file.path}</span>
								</button>
							))}
						</div>
						<div className="min-h-0 flex-1 overflow-auto bg-muted/20">
							{activeFile ? <DiffContent file={activeFile} /> : null}
						</div>
					</>
				)}
			</section>
		</div>
	);
}
