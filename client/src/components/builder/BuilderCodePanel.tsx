import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
	File,
	FileCode2,
	FileImage,
	FolderTree,
	Search,
} from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
	getRevisionFile,
	listRevisionFiles,
	type BuilderRevision,
} from "@/services/builder";
import { cn } from "@/lib/utils";

function fileIcon(path: string, isText: boolean) {
	if (!isText) return FileImage;
	if (/\.(tsx?|jsx?|css|json|ya?ml|md|py)$/i.test(path)) return FileCode2;
	return File;
}

export function BuilderCodePanel({
	solutionId,
	revision,
}: {
	solutionId: string;
	revision: BuilderRevision | null;
}) {
	const [search, setSearch] = useState("");
	const [selectedPath, setSelectedPath] = useState<string | null>(null);
	const filesQuery = useQuery({
		queryKey: ["builder", "revision-files", solutionId, revision?.id],
		queryFn: ({ signal }) =>
			listRevisionFiles(solutionId, revision!.id, { signal }),
		enabled: Boolean(revision),
	});
	const files = useMemo(() => filesQuery.data ?? [], [filesQuery.data]);
	const filteredFiles = files.filter((file) =>
		file.path.toLowerCase().includes(search.trim().toLowerCase()),
	);
	const activePath =
		files.some((file) => file.path === selectedPath)
			? selectedPath
			: (files[0]?.path ?? null);
	const activeFile = files.find((file) => file.path === activePath) ?? null;
	const contentQuery = useQuery({
		queryKey: [
			"builder",
			"revision-file",
			solutionId,
			revision?.id,
			activePath,
		],
		queryFn: ({ signal }) =>
			getRevisionFile(solutionId, revision!.id, activePath!, { signal }),
		enabled: Boolean(revision && activePath),
	});

	if (!revision) {
		return (
			<div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
				No source revision is available yet.
			</div>
		);
	}

	return (
		<div className="flex h-full min-h-0 flex-col bg-background md:flex-row">
			<aside className="flex max-h-56 w-full min-w-0 flex-col border-b md:max-h-none md:w-[250px] md:min-w-[190px] md:border-b-0 md:border-r">
				<div className="border-b p-2">
					<div className="relative">
						<Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
						<Input
							value={search}
							onChange={(event) => setSearch(event.target.value)}
							placeholder="Find a file"
							aria-label="Find a source file"
							className="h-8 pl-8 text-xs"
						/>
					</div>
				</div>
				<div className="flex items-center gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
					<FolderTree className="h-3.5 w-3.5" />
					<span className="truncate">Source {revision.id.slice(0, 8)}</span>
					<span className="ml-auto tabular-nums">{files.length}</span>
				</div>
				<div className="min-h-0 flex-1 overflow-auto py-1">
					{filesQuery.isLoading ? (
						<div className="space-y-1 p-2">
							<Skeleton className="h-7 w-full" />
							<Skeleton className="h-7 w-4/5" />
							<Skeleton className="h-7 w-11/12" />
						</div>
					) : filesQuery.isError ? (
						<div className="space-y-2 p-3">
							<p className="text-xs text-destructive">
								{(filesQuery.error as Error).message}
							</p>
							<Button
								variant="outline"
								size="sm"
								onClick={() => filesQuery.refetch()}
							>
								Try again
							</Button>
						</div>
					) : filteredFiles.length === 0 ? (
						<p className="p-3 text-xs text-muted-foreground">
							{files.length === 0 ? "This revision is empty." : "No files match."}
						</p>
					) : (
						filteredFiles.map((file) => {
							const Icon = fileIcon(file.path, file.is_text);
							return (
								<button
									type="button"
									key={file.path}
									className={cn(
										"flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-accent",
										activePath === file.path &&
											"bg-accent text-accent-foreground",
									)}
									onClick={() => setSelectedPath(file.path)}
									title={file.path}
								>
									<Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
									<span className="truncate">{file.path}</span>
								</button>
							);
						})
					)}
				</div>
			</aside>

			<section className="flex min-w-0 flex-1 flex-col">
				<div className="flex h-10 items-center border-b px-3 text-xs">
					<FileCode2 className="mr-2 h-3.5 w-3.5 text-muted-foreground" />
					<span className="truncate font-medium">
						{activePath ?? "Select a file"}
					</span>
					{contentQuery.data?.truncated ? (
						<span className="ml-auto text-amber-600 dark:text-amber-400">
							Preview truncated
						</span>
					) : null}
				</div>
				<div className="min-h-0 flex-1 overflow-auto bg-muted/20">
					{contentQuery.isLoading ? (
						<div className="space-y-2 p-4">
							<Skeleton className="h-4 w-3/4" />
							<Skeleton className="h-4 w-11/12" />
							<Skeleton className="h-4 w-2/3" />
						</div>
					) : contentQuery.isError ? (
						<div className="space-y-2 p-4">
							<p className="text-sm text-destructive">
								{(contentQuery.error as Error).message}
							</p>
							<Button
								variant="outline"
								size="sm"
								onClick={() => contentQuery.refetch()}
							>
								Try again
							</Button>
						</div>
					) : contentQuery.data?.encoding === "binary" ? (
						<div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
							{activeFile ? (
								<FileImage className="h-7 w-7 text-muted-foreground" />
							) : null}
							<p className="text-sm font-medium">Binary file</p>
							<p className="text-xs text-muted-foreground">
								Use Download source to inspect this file locally.
							</p>
						</div>
					) : (
						<pre
							className="min-h-full min-w-max p-4 font-mono text-[12px] leading-5 text-foreground"
							data-testid="builder-code-content"
						>
							<code>{contentQuery.data?.content ?? ""}</code>
						</pre>
					)}
				</div>
			</section>
		</div>
	);
}
