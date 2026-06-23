import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, Menu, Plus, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import {
	deleteFilePolicy,
	type FilePolicy,
} from "@/services/filePolicies";
import { useAuth } from "@/contexts/AuthContext";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useOrganizations } from "@/hooks/useOrganizations";
import { Breadcrumbs } from "./Breadcrumbs";
import { EffectiveAccessPanel } from "./EffectiveAccessPanel";
import { FilePreview } from "./FilePreview";
import { FolderListing, type ListingRowAction } from "./FolderListing";
import { NewShareDialog } from "./NewShareDialog";
import { PoliciesView } from "./PoliciesView";
import { PolicyEditorModal } from "./PolicyEditorModal";
import { ShareTree, type ShareTreeAction } from "./ShareTree";
import { TestAccessModal } from "./TestAccessModal";
import { useFileUpload } from "./useFileUpload";

const READ_ONLY_LOCATIONS = new Set(["uploads"]);
// Canonical surface for each explorer pane (matches shadcn Card: rounded-4xl
// ring instead of a hard border so it reads as part of the theme).
const PANE = "flex min-h-0 flex-col overflow-hidden rounded-[min(var(--radius-4xl),24px)] bg-card ring-1 ring-foreground/5 dark:ring-foreground/10";

interface FilesExplorerProps {
	/**
	 * When set, the explorer is pinned to the solution install's file scope:
	 * location="solutions", scope=<install_id>.  The org/global selector is
	 * hidden and the header shows a back-link to the Solution detail page.
	 */
	install?: string;
}

export function FilesExplorer({ install }: FilesExplorerProps = {}) {
	const { isPlatformAdmin } = useAuth();
	const { data: organizations = [] } = useOrganizations({
		enabled: isPlatformAdmin && !install,
	});
	const isWide = useMediaQuery("(min-width: 1024px)");

	// When `install` is set, scope and location are pinned — not user-controlled.
	// Otherwise selectorScope is what OrganizationSelect speaks: null = Global.
	// The data layer needs the EXPLICIT "global" string (not null/UNSET) so
	// write/upload (`resolve_target_org`) and the structural list agree — null
	// would resolve to the caller's own org on the write path while the explorer
	// means literal global.
	const [selectorScope, setSelectorScope] = useState<string | null>(null);
	const scope = install ?? selectorScope ?? "global";
	const [location, setLocation] = useState<string | null>(install ? "solutions" : null);
	const [prefix, setPrefix] = useState("");
	const [selectedFile, setSelectedFile] = useState<string | null>(null);
	const [view, setView] = useState<"browse" | "policies">("browse");

	const [treeOpen, setTreeOpen] = useState(false);
	const [detailOpen, setDetailOpen] = useState(false);
	const [newShareOpen, setNewShareOpen] = useState(false);
	const [testOpen, setTestOpen] = useState(false);
	const [policyOpen, setPolicyOpen] = useState(false);
	// The (location, path) a modal targets — may be a folder prefix or a file.
	const [modalTarget, setModalTarget] = useState<{ location: string; path: string }>(
		{ location: "", path: "" },
	);
	// Bump to force ShareTree/FolderListing to refetch after a mutation.
	const [refreshKey, setRefreshKey] = useState(0);

	const readOnly = location !== null && READ_ONLY_LOCATIONS.has(location);
	const canUpload = view === "browse" && location !== null && !readOnly;
	const uploadInputRef = useRef<HTMLInputElement>(null);
	const { uploading, uploadFiles } = useFileUpload(
		canUpload ? location : null,
		scope,
		prefix,
		() => setRefreshKey((k) => k + 1),
	);
	const scopeLabel = useMemo(() => {
		if (install) return "Solution";
		if (selectorScope === null) return "Global";
		return (
			organizations.find((o) => o.id === selectorScope)?.name ?? "Organization"
		);
	}, [install, selectorScope, organizations]);
	const segments = prefix ? prefix.replace(/\/$/, "").split("/") : [];

	function resetTo(nextLocation: string | null, nextPrefix: string) {
		setLocation(nextLocation);
		setPrefix(nextPrefix);
		setSelectedFile(null);
	}

	function handleScopeChange(next: string | null | undefined) {
		setSelectorScope(next ?? null);
		resetTo(null, "");
	}

	function handleSelect(nextLocation: string, nextPrefix: string) {
		resetTo(nextLocation, nextPrefix);
		setTreeOpen(false);
	}

	function handleBreadcrumb(depth: number) {
		if (depth === -1) {
			resetTo(null, "");
		} else if (depth === 0) {
			resetTo(location, "");
		} else {
			resetTo(location, segments.slice(0, depth).join("/"));
		}
	}

	function openTest(loc: string, path: string) {
		setModalTarget({ location: loc, path });
		setTestOpen(true);
	}

	function openPolicy(loc: string, path: string) {
		setModalTarget({ location: loc, path });
		setPolicyOpen(true);
	}

	async function handleDeletePolicy(policy: FilePolicy) {
		try {
			await deleteFilePolicy(policy);
			toast.success("File policy deleted");
			setRefreshKey((k) => k + 1);
		} catch (err) {
			toast.error("Failed to delete file policy", {
				description: err instanceof Error ? err.message : String(err),
			});
		}
	}

	function handleTreeAction(
		action: ShareTreeAction,
		loc: string,
		treePrefix: string,
	) {
		if (action === "effective") {
			handleSelect(loc, treePrefix);
		} else if (action === "test") {
			openTest(loc, treePrefix);
		} else if (action === "newPolicy") {
			openPolicy(loc, treePrefix);
		} else if (action === "upload") {
			handleSelect(loc, treePrefix);
		}
	}

	function handleRowAction(action: ListingRowAction, path: string) {
		if (location === null) return;
		if (action === "preview") {
			setSelectedFile(path);
			if (!isWide) setDetailOpen(true);
		} else if (action === "test") {
			openTest(location, path);
		} else if (action === "policy") {
			openPolicy(location, path);
		} else if (action === "delete") {
			void deleteFile(path);
		}
	}

	async function deleteFile(path: string) {
		if (location === null) return;
		const { files } = await import("@/lib/app-sdk/files");
		try {
			await files.delete(path, { location, scope });
			if (selectedFile === path) setSelectedFile(null);
			setRefreshKey((k) => k + 1);
		} catch {
			// surfaced by the SDK toast layer elsewhere; ignore here
		}
	}

	function selectFile(path: string) {
		setSelectedFile(path);
		if (!isWide) setDetailOpen(true);
	}

	const tree = (
		<ShareTree
			key={`tree-${scope}-${refreshKey}`}
			scope={scope}
			selectedLocation={location}
			selectedPrefix={prefix}
			onSelect={handleSelect}
			onContextAction={handleTreeAction}
		/>
	);

	const detail = (
		<div className="flex h-full min-h-0 flex-col" data-testid="detail-pane">
			<div className="min-h-0 flex-1 overflow-hidden border-b">
				<FilePreview location={location ?? ""} scope={scope} path={selectedFile} />
			</div>
			<div className="min-h-0 flex-1 overflow-hidden">
				<EffectiveAccessPanel
					location={location ?? ""}
					scope={scope}
					path={selectedFile ?? (location ? prefix : null)}
					onOpenTest={() =>
						openTest(location ?? "", selectedFile ?? prefix)
					}
					onManagePolicy={() =>
						openPolicy(location ?? "", selectedFile ?? prefix)
					}
				/>
			</div>
		</div>
	);

	return (
		<div className="flex h-full min-h-0 flex-col gap-3">
			<header className="flex shrink-0 flex-wrap items-center gap-2">
				{install && (
					<Link
						to={`/solutions/${install}`}
						className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
						data-testid="files-solution-back"
					>
						<ChevronLeft className="mr-1 h-3.5 w-3.5" />
						Solution
					</Link>
				)}
				{!isWide && (
					<Sheet open={treeOpen} onOpenChange={setTreeOpen}>
						<SheetTrigger asChild>
							<Button
								type="button"
								variant="outline"
								size="icon-sm"
								aria-label="Open shares"
							>
								<Menu className="h-4 w-4" />
							</Button>
						</SheetTrigger>
						<SheetContent side="left" className="w-72 p-0">
							<SheetHeader className="px-3 py-2">
								<SheetTitle>Shares</SheetTitle>
							</SheetHeader>
							<div className="flex min-h-0 flex-1 flex-col">{tree}</div>
						</SheetContent>
					</Sheet>
				)}
				{isPlatformAdmin && !install && (
					<div className="w-56">
						<OrganizationSelect
							value={selectorScope}
							onChange={handleScopeChange}
							showGlobal
							showAll={false}
						/>
					</div>
				)}
				{view === "browse" && (
					<Breadcrumbs
						scopeLabel={scopeLabel}
						location={location}
						segments={segments}
						onNavigate={handleBreadcrumb}
					/>
				)}
				<div className="ml-auto flex items-center gap-2">
					<Tabs
						value={view}
						onValueChange={(value) => setView(value as "browse" | "policies")}
					>
						<TabsList>
							<TabsTrigger value="browse">Browse</TabsTrigger>
							<TabsTrigger value="policies">Policies</TabsTrigger>
						</TabsList>
					</Tabs>
					<Button
						type="button"
						size="sm"
						variant="outline"
						onClick={() => setNewShareOpen(true)}
					>
						<Plus className="h-4 w-4" /> New Share
					</Button>
					{canUpload && (
						<>
							<input
								ref={uploadInputRef}
								type="file"
								multiple
								className="hidden"
								onChange={(event) => {
									if (event.target.files?.length)
										void uploadFiles(event.target.files);
									event.target.value = "";
								}}
							/>
							<Button
								type="button"
								size="sm"
								onClick={() => uploadInputRef.current?.click()}
								disabled={uploading}
							>
								<Upload className="h-4 w-4" />{" "}
								{uploading ? "Uploading…" : "Upload"}
							</Button>
						</>
					)}
				</div>
			</header>

			{view === "policies" ? (
				<div className="min-h-0 flex-1 overflow-hidden">
					<PoliciesView
						scope={scope}
						refreshKey={refreshKey}
						onEdit={(policy) => openPolicy(policy.location, policy.path)}
						onDelete={(policy) => void handleDeletePolicy(policy)}
					/>
				</div>
			) : (
				<div className="grid min-h-0 flex-1 gap-3 overflow-hidden lg:grid-cols-[18rem_minmax(0,1fr)_24rem]">
					{isWide && <div className={PANE}>{tree}</div>}
					{/* No PANE here: FolderListing's DataTable is its own card —
					    wrapping it in PANE would nest a card in a card. */}
					<div className="flex min-h-0 flex-col overflow-hidden">
						<FolderListing
							key={`listing-${scope}-${location}-${prefix}-${refreshKey}`}
							scope={scope}
							location={location}
							prefix={prefix}
							readOnly={readOnly}
							onOpenFolder={(next) => resetTo(location, next)}
							onSelectFile={selectFile}
							onRowAction={handleRowAction}
							onFolderAction={(action, folderPrefix) =>
								location !== null &&
								handleTreeAction(action, location, folderPrefix)
							}
							onUploaded={() => setRefreshKey((k) => k + 1)}
						/>
					</div>
					{isWide && <div className={PANE}>{detail}</div>}
				</div>
			)}

			{!isWide && (
				<Sheet open={detailOpen} onOpenChange={setDetailOpen}>
					<SheetContent side="right" className="w-full p-0 sm:max-w-md">
						<SheetHeader className="px-3 py-2">
							<SheetTitle>Details</SheetTitle>
						</SheetHeader>
						<div className="flex h-[calc(100%-3rem)] min-h-0 flex-col">
							{detail}
						</div>
					</SheetContent>
				</Sheet>
			)}

			<NewShareDialog
				open={newShareOpen}
				onOpenChange={setNewShareOpen}
				scope={scope}
				onCreated={(loc) => {
					setRefreshKey((k) => k + 1);
					handleSelect(loc, "");
				}}
			/>
			<TestAccessModal
				open={testOpen}
				onOpenChange={setTestOpen}
				location={modalTarget.location}
				scope={scope}
				path={modalTarget.path}
			/>
			<PolicyEditorModal
				open={policyOpen}
				onOpenChange={setPolicyOpen}
				location={modalTarget.location}
				scope={scope}
				path={modalTarget.path}
				onSaved={() => setRefreshKey((k) => k + 1)}
			/>
		</div>
	);
}
