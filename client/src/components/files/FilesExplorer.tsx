import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Menu, Plus, Upload } from "lucide-react";
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
	 * scope=<install_id>. The org/global selector is hidden and the header shows
	 * a back-link to the Solution detail page.
	 */
	install?: string;
	/** Display name for the pinned Solution install. */
	installName?: string;
}

export function FilesExplorer({ install, installName }: FilesExplorerProps = {}) {
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
	const [location, setLocation] = useState<string | null>(null);
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

	const solutionReadOnly = Boolean(install);
	const readOnly =
		solutionReadOnly || (location !== null && READ_ONLY_LOCATIONS.has(location));
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
		if (install && depth === 0) {
			resetTo(location, "");
			return;
		}
		if (install) {
			resetTo(location, segments.slice(0, depth).join("/"));
			return;
		}
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
		if (solutionReadOnly) return;
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
			if (solutionReadOnly) return;
			openPolicy(loc, treePrefix);
		} else if (action === "upload") {
			if (solutionReadOnly) return;
			handleSelect(loc, treePrefix);
		}
	}

	function handleRowAction(action: ListingRowAction, path: string) {
		if (location === null) return;
		if (readOnly && (action === "policy" || action === "delete")) return;
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
		if (location === null || readOnly) return;
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
			readOnly={solutionReadOnly}
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
					readOnly={readOnly}
					managedBySolution={solutionReadOnly}
					solutionId={install}
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
	const solutionTitle = installName ?? "Solution";
	const solutionBreadcrumbItems =
		location === null ? [] : [location, ...segments];

	return (
		<div className="flex h-full min-h-0 flex-col gap-3">
			<header className="flex shrink-0 flex-wrap items-center gap-2">
				<div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
					{install && (
						<>
							<Button asChild variant="outline" size="sm">
								<Link
									to={`/solutions/${install}`}
									data-testid="files-solution-back"
									aria-label="Back to Solution"
								>
									<ChevronLeft data-icon="inline-start" />
									Back
								</Link>
							</Button>
							<span
								className="min-w-0 truncate text-sm font-semibold text-foreground"
								title={solutionTitle}
							>
								{solutionTitle}
							</span>
						</>
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
						<div className="w-56 shrink-0">
							<OrganizationSelect
								value={selectorScope}
								onChange={handleScopeChange}
								showGlobal
								showAll={false}
							/>
						</div>
					)}
					{view === "browse" && !install && (
						<div className="min-w-0 flex-1">
							<Breadcrumbs
								scopeLabel={scopeLabel}
								location={location}
								segments={segments}
								onNavigate={handleBreadcrumb}
							/>
						</div>
					)}
					{view === "browse" && install && solutionBreadcrumbItems.length > 0 && (
						<nav
							aria-label="Breadcrumb"
							className="flex min-w-0 flex-1 flex-wrap items-center gap-0.5"
						>
							{solutionBreadcrumbItems.map((item, index) => (
								<span key={`${item}-${index}`} className="flex min-w-0 items-center">
									{index > 0 && (
										<ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
									)}
									<button
										type="button"
										onClick={() => handleBreadcrumb(index)}
										title={item}
										className={
											"max-w-[12rem] truncate rounded px-1 text-sm hover:bg-muted " +
											(index === solutionBreadcrumbItems.length - 1
												? "font-medium text-foreground"
												: "text-muted-foreground")
										}
									>
										{item}
									</button>
								</span>
							))}
						</nav>
					)}
				</div>
				<div className="ml-auto flex shrink-0 items-center gap-2">
					{!install && (
						<>
							<Tabs
								value={view}
								onValueChange={(value) =>
									setView(value as "browse" | "policies")
								}
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
						</>
					)}
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

			{view === "policies" && !install ? (
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
							managedBySolution={solutionReadOnly}
							solutionId={install}
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
