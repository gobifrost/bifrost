import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useReducedMotion } from "framer-motion";
import {
	Search,
	FileText,
	Workflow,
	FileCode,
	Loader2,
	AlertTriangle,
} from "lucide-react";
import { useEditorStore } from "@/stores/editorStore";
import { useAuth } from "@/contexts/AuthContext";
import { useForms } from "@/hooks/useForms";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { fileService } from "@/services/fileService";
import { searchService, type SearchFileHit } from "@/services/searchService";
import {
	CommandDialog,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];

interface SearchResult {
	type: "form" | "workflow" | "script";
	name: string;
	description: string;
	path?: string;
	id?: string;
}

interface QuickAccessProps {
	isOpen: boolean;
	onClose: () => void;
}

/**
 * Quick access command palette (Cmd+K)
 * Search and navigate to forms, workflows, and scripts
 */
export function QuickAccess({ isOpen, onClose }: QuickAccessProps) {
	const navigate = useNavigate();
	const { isPlatformAdmin } = useAuth();
	const openFileInTab = useEditorStore((state) => state.openFileInTab);
	const openEditor = useEditorStore((state) => state.openEditor);
	const setSidebarPanel = useEditorStore((state) => state.setSidebarPanel);

	const [query, setQuery] = useState("");
	const [results, setResults] = useState<SearchResult[]>([]);
	const [isSearching, setIsSearching] = useState(false);
	const [searchError, setSearchError] = useState<string | null>(null);
	const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
	const searchRunIdRef = useRef(0);
	const prefersReducedMotion = useReducedMotion();

	// Defer data fetching until QuickAccess is actually opened
	const { data: formsData } = useForms(undefined, { enabled: isOpen });
	// Only fetch workflows for platform admins (endpoint requires superuser)
	const { data: workflowsData } = useWorkflowsMetadata({
		enabled: isOpen && isPlatformAdmin,
	});

	// Search function
	const performSearch = useCallback(
		async (searchQuery: string) => {
			const runId = ++searchRunIdRef.current;
			if (!searchQuery.trim()) {
				setResults([]);
				setSearchError(null);
				setIsSearching(false);
				return;
			}

			setIsSearching(true);
			setSearchError(null);
			const queryLower = searchQuery.toLowerCase();
			const allResults: SearchResult[] = [];

			// Search forms by name - formsData is the array directly, not wrapped in an object
			if (formsData && Array.isArray(formsData)) {
				const formResults = formsData
					.filter(
						(form) =>
							form.name?.toLowerCase().includes(queryLower) ||
							form.description
								?.toLowerCase()
								.includes(queryLower),
					)
					.map((form) => ({
						type: "form" as const,
						name: form.name || "Untitled Form",
						description: form.description || "",
						id: form.id,
					}));
				allResults.push(...formResults);
			}

			// Search workflows by name
			if (workflowsData?.workflows) {
				const workflowResults = workflowsData.workflows
					.filter(
						(workflow: WorkflowMetadata) =>
							workflow.name?.toLowerCase().includes(queryLower) ||
							workflow.description
								?.toLowerCase()
								.includes(queryLower),
					)
					.map((workflow: WorkflowMetadata) => ({
						type: "workflow" as const,
						name: workflow.name || "Untitled Workflow",
						description: workflow.description || "",
					}));
				allResults.push(...workflowResults);
			}

			// Search scripts by filename and content
			try {
				const scriptsResponse = await searchService.searchFiles({
					query: searchQuery,
					case_sensitive: false,
					is_regex: false,
					source: "workspace",
					output_mode: "files",
					context_lines: 0,
					limit: 20,
				});

				const scriptResults = scriptsResponse.files.map(
					(hit: SearchFileHit) => ({
						type: "script" as const,
						name: hit.file_path.split("/").pop() || hit.file_path,
						description: `${hit.match_count} match${hit.match_count === 1 ? "" : "es"}, first at line ${hit.first_line}`,
						path: hit.file_path,
					}),
				);
				allResults.push(...scriptResults);
			} catch {
				if (runId === searchRunIdRef.current) {
					setSearchError("File search is temporarily unavailable");
				}
			}

			if (runId === searchRunIdRef.current) {
				setResults(allResults);
				setIsSearching(false);
			}
		},
		[formsData, workflowsData],
	);

	// Debounced search
	useEffect(() => {
		if (!isOpen) return;
		if (searchTimeoutRef.current) {
			clearTimeout(searchTimeoutRef.current);
		}

		searchTimeoutRef.current = setTimeout(() => {
			performSearch(query);
		}, 300); // 300ms debounce

		return () => {
			searchRunIdRef.current += 1;
			if (searchTimeoutRef.current) {
				clearTimeout(searchTimeoutRef.current);
			}
		};
	}, [query, performSearch, isOpen]);

	// Handle selection
	const handleSelect = useCallback(
		async (result: SearchResult) => {
			onClose();
			setQuery("");
			setResults([]);

			if (result.type === "form") {
				navigate(`/execute/${result.id}`);
			} else if (result.type === "workflow") {
				navigate(`/workflows/${result.name}/execute`);
			} else if (result.type === "script" && result.path) {
				// Open script in editor
				try {
					const fileResponse = await fileService.readFile(
						result.path,
					);
					const fileName =
						result.path.split("/").pop() || result.path;
					openEditor();
					openFileInTab(
						{
							name: fileName,
							path: result.path,
							type: "file",
							size: fileResponse.content.length,
							extension: fileName.includes(".")
								? fileName.split(".").pop() || null
								: null,
							modified: new Date().toISOString(),
							entity_type: null,
							entity_id: null,
						},
						fileResponse.content,
						fileResponse.encoding as "utf-8" | "base64",
					);
					// Switch to Run panel if it's a Python file
					if (fileName.endsWith(".py")) {
						setSidebarPanel("run");
					}
				} catch {
					// Silently handle file open error
				}
			}
		},
		[navigate, onClose, openEditor, openFileInTab, setSidebarPanel],
	);

	// Get icon for result type
	const getIcon = (type: SearchResult["type"]) => {
		switch (type) {
			case "form":
				return <FileText className="h-4 w-4" />;
			case "workflow":
				return <Workflow className="h-4 w-4" />;
			case "script":
				return <FileCode className="h-4 w-4" />;
		}
	};

	return (
		<CommandDialog
			open={isOpen}
			onOpenChange={(open) => {
				if (!open) onClose();
			}}
			title="Quick access"
			description="Search forms, workflows, and scripts"
			className="top-4! sm:top-1/3! sm:max-w-2xl"
			showCloseButton={false}
			// CommandDialog wraps children in <Command> itself; results come
			// server-ranked, so disable cmdk's own filtering on that wrapper.
			commandProps={{
				shouldFilter: false,
				className:
					"max-h-[calc(100dvh-2rem)] sm:max-h-[calc(66dvh-1rem)]",
			}}
		>
			<div className="relative shrink-0">
				<CommandInput
					placeholder="Search forms, workflows, and scripts..."
					value={query}
					onValueChange={setQuery}
				/>
				{isSearching && (
					<Loader2
						className={cn(
							"absolute top-1/2 right-4 size-4 -translate-y-1/2 text-muted-foreground",
							!prefersReducedMotion && "motion-safe:animate-spin",
						)}
					/>
				)}
			</div>

			<CommandList className="min-h-0 flex-1 max-h-[400px]">
				{searchError && (
					<div className="mx-2 mt-2 rounded-[var(--bf-radius-surface)] border border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)]/60 p-3 text-sm text-[var(--bf-warning)]">
						<div className="flex items-start gap-2">
							<AlertTriangle className="mt-0.5 size-4 shrink-0" />
							<div className="min-w-0 flex-1">
								<p className="leading-6">{searchError}</p>
								<p className="text-xs leading-5 text-muted-foreground">
									Forms and workflows still appear. Retry to
									search scripts again.
								</p>
							</div>
							<Button
								type="button"
								variant="outline"
								size="sm"
								className="min-h-11 shrink-0"
								onClick={() => performSearch(query)}
								disabled={isSearching}
							>
								Retry
							</Button>
						</div>
					</div>
				)}

				{results.length === 0 &&
					query.trim() !== "" &&
					!isSearching &&
					!searchError && (
						<div className="px-4 py-8 text-center text-sm text-muted-foreground">
							No results found
						</div>
					)}

				{results.length === 0 && query.trim() === "" && (
					<div className="px-4 py-8 text-center">
						<Search className="h-12 w-12 mx-auto mb-3 text-muted-foreground/50" />
						<p className="text-sm text-muted-foreground">
							Search for forms, workflows, and scripts
						</p>
						<p className="text-xs text-muted-foreground mt-2">
							Use ↑↓ to navigate, Enter to select, Esc to close
						</p>
					</div>
				)}

				{results.length > 0 && (
					<CommandGroup>
						{results.map((result, index) => (
							<CommandItem
								key={`${result.type}-${result.name}-${index}`}
								value={`${result.type}-${result.name}-${index}`}
								onSelect={() => handleSelect(result)}
								className="min-h-11 items-start gap-3 px-3 py-2.5"
							>
								<div className="mt-0.5 text-muted-foreground">
									{getIcon(result.type)}
								</div>
								<div className="flex-1 min-w-0">
									<div className="flex items-center gap-2">
										<span className="font-medium text-sm leading-6 [overflow-wrap:anywhere]">
											{result.name}
										</span>
										<span className="text-xs leading-5 text-muted-foreground capitalize">
											{result.type}
										</span>
									</div>
									{result.description && (
										<p className="mt-1 text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">
											{result.description}
										</p>
									)}
								</div>
							</CommandItem>
						))}
					</CommandGroup>
				)}
			</CommandList>

			{/* Footer hint */}
			{results.length > 0 && (
				<div className="hidden shrink-0 items-center justify-between border-t border-border/50 px-4 py-2 text-xs text-muted-foreground sm:flex">
					<span>↑↓ to navigate</span>
					<span>Enter to select</span>
					<span>Esc to close</span>
				</div>
			)}
			<div className="flex shrink-0 justify-end border-t border-border/50 p-1 sm:hidden">
				<Button variant="ghost" className="min-h-11" onClick={onClose}>
					Close search
				</Button>
			</div>
		</CommandDialog>
	);
}
