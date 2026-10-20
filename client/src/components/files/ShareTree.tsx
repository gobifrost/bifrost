import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
	ChevronDown,
	ChevronRight,
	Folder,
	HardDrive,
	Lock,
	MoreHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import {
	DropdownMenu,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { listShares, listStructure } from "@/services/fileStructure";
import {
	ENTRY_ACTION_META,
	EntryMenuItem,
	FileContextMenuContent,
	FileDropdownMenuContent,
} from "./fileContextMenu";
import { InlineLoader } from "./InlineLoader";

export type ShareTreeAction =
	"effective" | "test" | "newFolder" | "upload" | "newPolicy";

interface ShareTreeProps {
	scope: string | null;
	solution?: string;
	selectedLocation: string | null;
	selectedPrefix: string;
	readOnly?: boolean;
	policyReadOnly?: boolean;
	onSelect: (location: string, prefix: string) => void;
	onContextAction: (
		action: ShareTreeAction,
		location: string,
		prefix: string,
	) => void;
}

interface NodeProps extends ShareTreeProps {
	location: string;
	prefix: string;
	name: string;
	depth: number;
	branchLast?: boolean;
}

function loadStructure(
	location: string,
	prefix: string,
	scope: string | null,
	solution?: string,
) {
	return solution
		? listStructure(location, prefix, scope, solution)
		: listStructure(location, prefix, scope);
}

function loadShares(scope: string | null, solution?: string) {
	return solution ? listShares(scope, solution) : listShares(scope);
}

function LoadError({
	label,
	retry,
	pending,
}: {
	label: string;
	retry: () => void;
	pending: boolean;
}) {
	return (
		<div role="alert" className="space-y-1 px-2 py-2 text-sm">
			<p className="text-destructive">{label} could not be loaded.</p>
			<Button
				variant="outline"
				className="min-h-11"
				disabled={pending}
				onClick={retry}
			>
				Retry {label.toLowerCase()}
			</Button>
		</div>
	);
}

function BrowseNode(props: NodeProps) {
	const {
		location,
		prefix,
		name,
		depth,
		scope,
		solution,
		readOnly,
		policyReadOnly = false,
		selectedLocation,
		selectedPrefix,
		onSelect,
		onContextAction,
		branchLast = false,
	} = props;
	const active = selectedLocation === location;
	const [expanded, setExpanded] = useState(
		active && (prefix === "" || selectedPrefix.startsWith(`${prefix}/`)),
	);
	const selected = active && selectedPrefix === prefix;
	const children = useQuery({
		queryKey: ["file-structure", scope, solution, location, prefix],
		queryFn: () => loadStructure(location, prefix, scope, solution),
		enabled: expanded,
		retry: false,
	});
	const folders =
		children.data?.filter((entry) => entry.kind === "folder") ?? [];
	const actions: ShareTreeAction[] = readOnly
		? ["effective", "test"]
		: policyReadOnly
			? ["effective", "test", "upload"]
			: ["effective", "test", "upload", "newPolicy"];
	const Icon = prefix === "" ? HardDrive : Folder;
	const rowIndent = `calc(var(--share-tree-indent) * ${Math.min(depth, 4)})`;

	return (
		<li
			className={cn(
				"relative min-w-0",
				depth > 0 &&
					"before:absolute before:left-[var(--row-indent)] before:top-0 before:h-full before:w-px before:bg-border/80 after:absolute after:left-[var(--row-indent)] after:top-5 after:h-px after:w-4 after:bg-border/80",
				branchLast && "before:h-5",
			)}
			style={
				{
					"--row-indent": rowIndent,
				} as React.CSSProperties
			}
		>
			<ContextMenu>
				<ContextMenuTrigger asChild>
					<div
						className={cn(
							"relative flex min-w-0 items-center border-b border-border/45 transition-colors duration-(--bf-motion-feedback) motion-reduce:transition-none",
							selected
								? "z-20 tree-row-selected"
								: "hover:bg-muted/25",
						)}
						style={{
							paddingLeft: rowIndent,
						}}
					>
						<Button
							variant="ghost"
							size="icon-lg"
							className="size-9 shrink-0"
							aria-label={`${expanded ? "Collapse" : "Expand"} ${name}`}
							aria-expanded={expanded}
							onClick={() => setExpanded((value) => !value)}
						>
							{expanded ? (
								<ChevronDown aria-hidden="true" />
							) : (
								<ChevronRight aria-hidden="true" />
							)}
						</Button>
						<button
							type="button"
							aria-current={selected ? "location" : undefined}
							onClick={() => onSelect(location, prefix)}
							className="flex min-h-10 min-w-0 flex-1 items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-2 text-left text-sm transition-colors duration-(--bf-motion-feedback) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
						>
							<Icon
								aria-hidden="true"
								className={cn(
									"size-4 shrink-0",
									prefix === ""
										? "text-primary"
										: "text-amber-600 dark:text-amber-400",
								)}
							/>
							<span className="min-w-0">
								<span
									className={cn(
										"block truncate",
										selected
											? "font-semibold text-foreground"
											: "font-medium text-foreground",
									)}
								>
									{name}
								</span>
								{prefix === "" && readOnly && (
									<span className="mt-0.5 flex items-center gap-1 text-xs font-normal text-muted-foreground">
										<Lock
											aria-hidden="true"
											className="size-3"
										/>
										Read-only
									</span>
								)}
							</span>
						</button>
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<Button
									variant="ghost"
									size="icon-lg"
									className="size-9 shrink-0"
									aria-label={`Actions for ${name}`}
								>
									<MoreHorizontal aria-hidden="true" />
								</Button>
							</DropdownMenuTrigger>
							<FileDropdownMenuContent align="end">
								{actions.map((action) => {
									const meta = ENTRY_ACTION_META[action];
									const ActionIcon = meta.icon;
									return (
										<DropdownMenuItem
											key={action}
											className="min-h-11"
											onSelect={() =>
												onContextAction(
													action,
													location,
													prefix,
												)
											}
										>
											<ActionIcon aria-hidden="true" />
											{meta.label}
										</DropdownMenuItem>
									);
								})}
							</FileDropdownMenuContent>
						</DropdownMenu>
					</div>
				</ContextMenuTrigger>
				<FileContextMenuContent>
					{actions.map((action) => (
						<EntryMenuItem
							key={action}
							action={action}
							onSelect={() =>
								onContextAction(action, location, prefix)
							}
						/>
					))}
				</FileContextMenuContent>
			</ContextMenu>
			{expanded && (
				<div className="relative min-w-0">
					{children.isPending && (
						<InlineLoader
							className="px-2 py-3"
							label={`Loading folders in ${name}…`}
						/>
					)}
					{children.isError && (
						<LoadError
							label="Folders"
							retry={() => void children.refetch()}
							pending={children.isFetching}
						/>
					)}
					{children.isSuccess && folders.length === 0 && (
						<p
							className="border-b border-border/45 px-2 py-3 text-xs text-muted-foreground"
							style={{ paddingLeft: rowIndent }}
						>
							No subfolders
						</p>
					)}
					{folders.length > 0 && (
						<ol
							aria-label={`Folders in ${name}`}
							className="min-w-0"
						>
							{folders.map((folder, index) => (
								<BrowseNode
									{...props}
									key={folder.path}
									prefix={folder.path}
									name={folder.name}
									depth={depth + 1}
									branchLast={index === folders.length - 1}
								/>
							))}
						</ol>
					)}
				</div>
			)}
		</li>
	);
}

function ShareNavigation(props: ShareTreeProps) {
	const shares = useQuery({
		queryKey: ["file-shares", props.scope, props.solution],
		queryFn: () => loadShares(props.scope, props.solution),
		retry: false,
	});
	return (
		<nav
			aria-label="File shares"
			className="min-h-0 min-w-0 flex-1 overflow-auto [--share-tree-indent:1rem]"
		>
			{shares.isPending && (
				<InlineLoader className="px-2 py-3" label="Loading shares…" />
			)}
			{shares.isError && (
				<LoadError
					label="Shares"
					retry={() => void shares.refetch()}
					pending={shares.isFetching}
				/>
			)}
			{shares.isSuccess && shares.data.length === 0 && (
				<p className="px-2 py-3 text-sm text-muted-foreground">
					{props.readOnly
						? "No shares in this scope."
						: "No shares in this scope. Create one with “New share”."}
				</p>
			)}
			{!!shares.data?.length && (
				<ol className="min-w-0">
					{shares.data.map((share) => (
						<BrowseNode
							{...props}
							key={share.location}
							location={share.location}
							prefix=""
							name={share.location}
							depth={0}
							readOnly={props.readOnly || share.readOnly}
						/>
					))}
				</ol>
			)}
		</nav>
	);
}

export function ShareTree(props: ShareTreeProps) {
	return (
		<ShareNavigation
			key={`${props.scope ?? "global"}:${props.solution ?? ""}`}
			{...props}
		/>
	);
}
