import { useEffect, useState } from "react";
import {
	ChevronDown,
	ChevronRight,
	Folder,
	HardDrive,
	Lock,
} from "lucide-react";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
	listShares,
	listStructure,
	type ShareEntry,
	type StructureEntry,
} from "@/services/fileStructure";
import { EntryMenuItem } from "./fileContextMenu";
import { InlineLoader } from "./InlineLoader";

export type ShareTreeAction =
	| "effective"
	| "test"
	| "newFolder"
	| "upload"
	| "newPolicy";

interface ShareTreeProps {
	scope: string | null;
	selectedLocation: string | null;
	selectedPrefix: string;
	onSelect: (location: string, prefix: string) => void;
	onContextAction: (
		action: ShareTreeAction,
		location: string,
		prefix: string,
	) => void;
}

interface FolderNodeProps {
	location: string;
	prefix: string;
	name: string;
	depth: number;
	scope: string | null;
	readOnly: boolean;
	selectedLocation: string | null;
	selectedPrefix: string;
	onSelect: ShareTreeProps["onSelect"];
	onContextAction: ShareTreeProps["onContextAction"];
}

function FolderNode({
	location,
	prefix,
	name,
	depth,
	scope,
	readOnly,
	selectedLocation,
	selectedPrefix,
	onSelect,
	onContextAction,
}: FolderNodeProps) {
	const [expanded, setExpanded] = useState(false);
	const [children, setChildren] = useState<StructureEntry[] | null>(null);
	const selected =
		selectedLocation === location && selectedPrefix === prefix;

	useEffect(() => {
		let cancelled = false;
		if (!expanded || children !== null) return;
		listStructure(location, prefix, scope)
			.then((entries) => {
				if (!cancelled)
					setChildren(entries.filter((e) => e.kind === "folder"));
			})
			.catch(() => {
				if (!cancelled) setChildren([]);
			});
		return () => {
			cancelled = true;
		};
	}, [expanded, children, location, prefix, scope]);

	return (
		<div>
			<ContextMenu>
				<ContextMenuTrigger asChild>
					<div
						role="treeitem"
						aria-selected={selected}
						className={
							"flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1 text-sm hover:bg-muted " +
							(selected ? "bg-muted font-medium" : "")
						}
						style={{ paddingLeft: `${depth * 12 + 4}px` }}
						onClick={() => {
							onSelect(location, prefix);
							setExpanded((value) => !value);
						}}
					>
						{expanded ? (
							<ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
						) : (
							<ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
						)}
						<Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
						<span className="truncate" title={name}>
							{name}
						</span>
					</div>
				</ContextMenuTrigger>
				<ContextMenuContent>
					<EntryMenuItem action="effective" onSelect={() => onContextAction("effective", location, prefix)} />
					<EntryMenuItem action="test" onSelect={() => onContextAction("test", location, prefix)} />
					{!readOnly && (
						<>
							<EntryMenuItem action="upload" onSelect={() => onContextAction("upload", location, prefix)} />
							<EntryMenuItem action="newPolicy" onSelect={() => onContextAction("newPolicy", location, prefix)} />
						</>
					)}
				</ContextMenuContent>
			</ContextMenu>
			{expanded &&
				(children ?? []).map((child) => (
					<FolderNode
						key={child.path}
						location={location}
						prefix={child.path}
						name={child.name}
						depth={depth + 1}
						scope={scope}
						readOnly={readOnly}
						selectedLocation={selectedLocation}
						selectedPrefix={selectedPrefix}
						onSelect={onSelect}
						onContextAction={onContextAction}
					/>
				))}
		</div>
	);
}

export function ShareTree({
	scope,
	selectedLocation,
	selectedPrefix,
	onSelect,
	onContextAction,
}: ShareTreeProps) {
	const [shares, setShares] = useState<ShareEntry[]>([]);
	const [loading, setLoading] = useState(false);

	useEffect(() => {
		let cancelled = false;
		void (async () => {
			setLoading(true);
			try {
				const result = await listShares(scope);
				if (!cancelled) setShares(result);
			} catch {
				if (!cancelled) setShares([]);
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [scope]);

	return (
		<div role="tree" className="min-h-0 flex-1 overflow-auto p-2">
			{loading && shares.length === 0 && (
				<InlineLoader className="px-2 py-1" label="Loading shares…" />
			)}
			{!loading && shares.length === 0 && (
				<p className="px-2 py-1 text-xs text-muted-foreground">
					No shares in this scope. Create one with “New share”.
				</p>
			)}
			{shares.map((share) => {
				const locationActive = selectedLocation === share.location;
				const selected = locationActive && selectedPrefix === "";
				return (
					<div key={share.location}>
						<ContextMenu>
							<ContextMenuTrigger asChild>
								<div
									role="treeitem"
									aria-selected={selected}
									className={
										"flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1 text-sm hover:bg-muted " +
										(selected ? "bg-muted font-medium" : "")
									}
									onClick={() => onSelect(share.location, "")}
								>
									<HardDrive className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
									<span className="truncate" title={share.location}>
										{share.location}
									</span>
									{share.readOnly && (
										<span className="ml-auto flex items-center gap-0.5 text-[10px] text-muted-foreground">
											<Lock className="h-3 w-3" /> read-only
										</span>
									)}
								</div>
							</ContextMenuTrigger>
							<ContextMenuContent>
								<EntryMenuItem action="effective" onSelect={() => onContextAction("effective", share.location, "")} />
								<EntryMenuItem action="test" onSelect={() => onContextAction("test", share.location, "")} />
								{!share.readOnly && (
									<>
										<EntryMenuItem action="upload" onSelect={() => onContextAction("upload", share.location, "")} />
										<EntryMenuItem action="newPolicy" onSelect={() => onContextAction("newPolicy", share.location, "")} />
									</>
								)}
							</ContextMenuContent>
						</ContextMenu>
						{locationActive && (
							<ShareChildren
								location={share.location}
								scope={scope}
								readOnly={share.readOnly}
								selectedLocation={selectedLocation}
								selectedPrefix={selectedPrefix}
								onSelect={onSelect}
								onContextAction={onContextAction}
							/>
						)}
					</div>
				);
			})}
		</div>
	);
}

function ShareChildren({
	location,
	scope,
	readOnly,
	selectedLocation,
	selectedPrefix,
	onSelect,
	onContextAction,
}: {
	location: string;
	scope: string | null;
	readOnly: boolean;
	selectedLocation: string | null;
	selectedPrefix: string;
	onSelect: ShareTreeProps["onSelect"];
	onContextAction: ShareTreeProps["onContextAction"];
}) {
	const [folders, setFolders] = useState<StructureEntry[]>([]);

	useEffect(() => {
		let cancelled = false;
		listStructure(location, "", scope)
			.then((entries) => {
				if (!cancelled)
					setFolders(entries.filter((e) => e.kind === "folder"));
			})
			.catch(() => {
				if (!cancelled) setFolders([]);
			});
		return () => {
			cancelled = true;
		};
	}, [location, scope]);

	return (
		<>
			{folders.map((folder) => (
				<FolderNode
					key={folder.path}
					location={location}
					prefix={folder.path}
					name={folder.name}
					depth={1}
					scope={scope}
					readOnly={readOnly}
					selectedLocation={selectedLocation}
					selectedPrefix={selectedPrefix}
					onSelect={onSelect}
					onContextAction={onContextAction}
				/>
			))}
		</>
	);
}
