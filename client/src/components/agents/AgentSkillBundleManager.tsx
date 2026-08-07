import {
	useCallback,
	useMemo,
	useRef,
	useState,
	type DragEvent,
	type ChangeEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Archive,
	Code2,
	Eye,
	FileArchive,
	Loader2,
	Lock,
	RefreshCw,
	Upload,
} from "lucide-react";
import { toast } from "sonner";

import { FileTree } from "@/components/file-tree/FileTree";
import type {
	FileContent,
	FileNode,
	FileOperations,
} from "@/components/file-tree/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import {
	ToggleGroup,
	ToggleGroupItem,
} from "@/components/ui/toggle-group";
import {
	detachAgentSkill,
	getAgentSkill,
	getAgentSkillFile,
	uploadAgentSkill,
	type AgentSkill,
} from "@/services/agentSkills";
import { cn } from "@/lib/utils";

const skillQueryKey = (agentId: string) => ["agent-skill", agentId] as const;
type MarkdownView = "preview" | "source";

function extension(path: string): string | null {
	const name = path.split("/").at(-1) ?? path;
	const dot = name.lastIndexOf(".");
	return dot > 0 ? name.slice(dot + 1) : null;
}

function unavailableMutation(): Promise<never> {
	return Promise.reject(new Error("This Skill bundle is read-only"));
}

function makeOperations(agentId: string, files: string[]): FileOperations {
	return {
		list: async () =>
			files.map((path): FileNode => ({
				path,
				name: path.split("/").at(-1) ?? path,
				type: "file",
				size: null,
				extension: extension(path),
				modified: null,
			})),
		read: async (path) => getAgentSkillFile(agentId, path),
		write: unavailableMutation,
		createFolder: unavailableMutation,
		delete: unavailableMutation,
		rename: unavailableMutation,
	};
}

function sourceLabel(skill: AgentSkill): string {
	if (skill.source === "solution") return "Managed by Solution";
	if (skill.source === "upload") return "Uploaded bundle";
	return "Inline instructions";
}

function withoutSkillFrontmatter(markdown: string): string {
	const normalized = markdown.replace(/\r\n/g, "\n");
	if (!normalized.startsWith("---\n")) return markdown;
	const closing = normalized.indexOf("\n---\n", 4);
	return closing < 0 ? markdown : normalized.slice(closing + 5).trimStart();
}

interface AgentSkillBundleManagerProps {
	agentId?: string;
	isSolutionManaged?: boolean;
}

export function AgentSkillBundleManager({
	agentId,
	isSolutionManaged = false,
}: AgentSkillBundleManagerProps) {
	const queryClient = useQueryClient();
	const inputRef = useRef<HTMLInputElement>(null);
	const [isDragging, setIsDragging] = useState(false);
	const [selectedPath, setSelectedPath] = useState("SKILL.md");
	const [selectedContent, setSelectedContent] = useState<FileContent | null>(
		null,
	);
	const [markdownView, setMarkdownView] =
		useState<MarkdownView>("preview");
	const [removeOpen, setRemoveOpen] = useState(false);

	const skillQuery = useQuery({
		queryKey: skillQueryKey(agentId ?? "new"),
		queryFn: ({ signal }) => getAgentSkill(agentId!, { signal }),
		enabled: Boolean(agentId),
	});
	const skill = skillQuery.data;
	const operations = useMemo(
		() => makeOperations(agentId ?? "", skill?.files ?? []),
		[agentId, skill?.files],
	);

	const refreshAgent = useCallback(async () => {
		if (!agentId) return;
		await Promise.all([
			queryClient.invalidateQueries({ queryKey: skillQueryKey(agentId) }),
			queryClient.invalidateQueries({ queryKey: ["get", "/api/agents"] }),
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/agents/{agent_id}"],
			}),
		]);
	}, [agentId, queryClient]);

	const uploadMutation = useMutation({
		mutationFn: (file: File) => uploadAgentSkill(agentId!, file),
		onSuccess: async (nextSkill) => {
			queryClient.setQueryData(skillQueryKey(agentId!), nextSkill);
			setSelectedPath("SKILL.md");
			setSelectedContent({
				content: nextSkill.skill_markdown,
				encoding: "utf-8",
			});
			setMarkdownView("preview");
			await refreshAgent();
			toast.success("Agent Skill uploaded", {
				description: "SKILL.md is now the agent's canonical instructions.",
			});
		},
		onError: (error: Error) => toast.error(error.message),
	});
	const detachMutation = useMutation({
		mutationFn: () => detachAgentSkill(agentId!),
		onSuccess: async () => {
			setRemoveOpen(false);
			setSelectedContent(null);
			await refreshAgent();
			toast.success("Skill bundle removed", {
				description: "The SKILL.md body was preserved as inline instructions.",
			});
		},
		onError: (error: Error) => toast.error(error.message),
	});

	const acceptFile = useCallback(
		(file: File | undefined) => {
			if (!file || !agentId || isSolutionManaged) return;
			const lower = file.name.toLowerCase();
			if (!lower.endsWith(".zip") && !lower.endsWith(".skill")) {
				toast.error("Upload a .skill or .zip archive");
				return;
			}
			uploadMutation.mutate(file);
		},
		[agentId, isSolutionManaged, uploadMutation],
	);

	function handleDrop(event: DragEvent<HTMLDivElement>) {
		event.preventDefault();
		setIsDragging(false);
		acceptFile(event.dataTransfer.files[0]);
	}

	function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
		acceptFile(event.target.files?.[0]);
		event.target.value = "";
	}

	if (!agentId) {
		return (
			<Alert>
				<FileArchive className="h-4 w-4" />
				<AlertTitle>Save this agent before adding a bundle</AlertTitle>
				<AlertDescription>
					After creation, you can drag in a .skill or .zip archive here.
				</AlertDescription>
			</Alert>
		);
	}

	if (skillQuery.isLoading) {
		return <Skeleton className="h-44 w-full" />;
	}
	if (skillQuery.isError || !skill) {
		return (
			<Alert variant="destructive">
				<AlertTitle>Agent Skill could not be loaded</AlertTitle>
				<AlertDescription className="mt-2">
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={() => skillQuery.refetch()}
					>
						<RefreshCw className="mr-2 h-3.5 w-3.5" />
						Try again
					</Button>
				</AlertDescription>
			</Alert>
		);
	}

	const hasBundle = Boolean(skill.bundle_path);
	const managed = isSolutionManaged || skill.is_managed;
	const busy = uploadMutation.isPending || detachMutation.isPending;
	const isMarkdown = selectedPath.toLowerCase().endsWith(".md");
	const selectedText =
		selectedContent?.encoding === "base64"
			? null
			: (selectedContent?.content ??
				(selectedPath === "SKILL.md" ? skill.skill_markdown : ""));
	const renderedMarkdown =
		selectedText !== null && selectedPath.toLowerCase().endsWith("skill.md")
			? withoutSkillFrontmatter(selectedText)
			: (selectedText ?? "");

	return (
		<div className="space-y-3">
			<div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border bg-muted/20 p-3">
				<div className="min-w-0">
					<div className="flex items-center gap-2">
						<Archive className="h-4 w-4 text-primary" />
						<p className="text-sm font-medium">{skill.name}</p>
						<Badge variant="outline">{sourceLabel(skill)}</Badge>
					</div>
					<p className="mt-1 text-xs text-muted-foreground">
						{hasBundle
							? "SKILL.md supplies the instructions. Bundle files are available to the agent at runtime."
							: "Instructions are editable below and export as a portable SKILL.md."}
					</p>
					{skill.bundle_path ? (
						<code className="mt-2 block truncate text-xs text-muted-foreground">
							{skill.bundle_path}
						</code>
					) : null}
				</div>
				{hasBundle && !managed ? (
					<div className="flex gap-2">
						<Button
							type="button"
							size="sm"
							variant="outline"
							disabled={busy}
							onClick={() => inputRef.current?.click()}
						>
							{uploadMutation.isPending ? (
								<Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
							) : (
								<Upload className="mr-2 h-3.5 w-3.5" />
							)}
							Replace
						</Button>
						<Button
							type="button"
							size="sm"
							variant="ghost"
							disabled={busy}
							onClick={() => setRemoveOpen(true)}
						>
							Remove bundle
						</Button>
					</div>
				) : null}
			</div>

			{hasBundle ? (
				<div className="grid min-h-64 overflow-hidden rounded-xl border md:grid-cols-[220px_minmax(0,1fr)]">
					<div className="h-64 border-b bg-muted/10 md:border-r md:border-b-0">
						<FileTree
							operations={operations}
							config={{
								enableCreate: false,
								enableDelete: false,
								enableDragMove: false,
								enableRename: false,
								emptyMessage: "This bundle has no files.",
							}}
							editor={{
								isFileSelected: (path) => path === selectedPath,
								onFileOpen: (file, content) => {
									setSelectedPath(file.path);
									setSelectedContent(content);
									setMarkdownView(
										file.path.toLowerCase().endsWith(".md")
											? "preview"
											: "source",
									);
								},
							}}
						/>
					</div>
					<div className="min-w-0 bg-background">
						<div className="flex h-10 items-center border-b px-3">
							<code className="truncate text-xs">{selectedPath}</code>
							<div className="ml-auto flex items-center gap-2">
								{isMarkdown && selectedText !== null ? (
									<ToggleGroup
										type="single"
										value={markdownView}
										onValueChange={(nextView) => {
											if (
												nextView === "preview" ||
												nextView === "source"
											) {
												setMarkdownView(nextView);
											}
										}}
										variant="outline"
										size="sm"
										aria-label="Markdown file display"
									>
										<ToggleGroupItem
											value="preview"
											aria-label="Preview Markdown"
											className="h-7 px-2 text-xs"
										>
											<Eye className="mr-1 h-3 w-3" />
											Preview
										</ToggleGroupItem>
										<ToggleGroupItem
											value="source"
											aria-label="View Markdown source"
											className="h-7 px-2 text-xs"
										>
											<Code2 className="mr-1 h-3 w-3" />
											Source
										</ToggleGroupItem>
									</ToggleGroup>
								) : null}
								<span className="hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
									<Lock className="h-3 w-3" />
									Read-only
								</span>
							</div>
						</div>
						{isMarkdown &&
						selectedText !== null &&
						markdownView === "preview" ? (
							<TiptapEditor
								content={renderedMarkdown}
								readOnly
								ariaLabel={`Preview ${selectedPath}`}
								className="h-[216px] rounded-none border-0"
							/>
						) : (
							<pre className="h-[216px] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5">
								{selectedText ?? "Binary file — preview unavailable"}
							</pre>
						)}
					</div>
				</div>
			) : managed ? (
				<Alert>
					<Lock className="h-4 w-4" />
					<AlertTitle>Managed inline instructions</AlertTitle>
					<AlertDescription>
						This Solution does not include a companion bundle. Update the
						Solution source and redeploy to change its instructions.
					</AlertDescription>
				</Alert>
			) : (
				<div
					className={cn(
						"rounded-xl border border-dashed px-6 py-8 text-center transition-colors",
						isDragging && "border-primary bg-primary/5",
					)}
					onDragEnter={(event) => {
						event.preventDefault();
						setIsDragging(true);
					}}
					onDragOver={(event) => event.preventDefault()}
					onDragLeave={() => setIsDragging(false)}
					onDrop={handleDrop}
				>
					<FileArchive className="mx-auto h-7 w-7 text-muted-foreground" />
					<p className="mt-3 text-sm font-medium">
						Drop a .skill or .zip bundle
					</p>
					<p className="mx-auto mt-1 max-w-md text-xs leading-5 text-muted-foreground">
						We validate SKILL.md and portable assets, references, and scripts
						before attaching the bundle to this agent.
					</p>
					<Button
						type="button"
						variant="outline"
						size="sm"
						className="mt-4"
						disabled={busy}
						onClick={() => inputRef.current?.click()}
					>
						{uploadMutation.isPending ? (
							<Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
						) : (
							<Upload className="mr-2 h-3.5 w-3.5" />
						)}
						Choose archive
					</Button>
				</div>
			)}

			<input
				ref={inputRef}
				type="file"
				accept=".skill,.zip,application/zip"
				className="sr-only"
				aria-label="Upload Agent Skill archive"
				onChange={handleFileChange}
			/>

			<AlertDialog open={removeOpen} onOpenChange={setRemoveOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Remove this Skill bundle?</AlertDialogTitle>
						<AlertDialogDescription>
							Companion files will be removed. The SKILL.md instruction body
							will remain on the agent as editable inline instructions.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								detachMutation.mutate();
							}}
						>
							{detachMutation.isPending ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							) : null}
							Remove bundle
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
