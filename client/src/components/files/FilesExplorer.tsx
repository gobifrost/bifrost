import { useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import { FilesInspector } from "./FilesInspector";
import {
	ChevronLeft,
	Menu,
	Plus,
	Upload,
	Info,
	FolderOpen,
	ShieldCheck,
	HardDrive,
} from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { WorkspacePrimaryAction } from "@/components/layout/WorkspacePrimaryAction";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { type FilePolicy } from "@/services/filePolicies";
import { useAuthorization } from "@/services/authorization";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useOrganizations } from "@/hooks/useOrganizations";
import { Breadcrumbs } from "./Breadcrumbs";
import { EffectiveAccessPanel } from "./EffectiveAccessPanel";
import { FilePreview } from "./FilePreview";
import { FolderListing, type ListingRowAction } from "./FolderListing";
import {
	DeleteConfirmation,
	type FileDeleteTarget,
} from "./DeleteConfirmation";
import { NewShareDialog } from "./NewShareDialog";
import { PoliciesView } from "./PoliciesView";
import { PolicyEditorPanel } from "./PolicyEditorModal";
import { SharesOverview } from "./SharesOverview";
import { ShareTree, type ShareTreeAction } from "./ShareTree";
import { TestAccessPanel } from "./TestAccessModal";
import { useFileUpload } from "./useFileUpload";

const READ_ONLY_LOCATIONS = new Set(["uploads"]);

interface FilesExplorerProps {
	/**
	 * When set, the explorer is pinned to the solution install's file scope:
	 * scope=<install_id>. The org/global selector is hidden and the header shows
	 * a back-link to the Solution detail page.
	 */
	install?: string;
	/** Display name for the pinned Solution install. */
	installName?: string;
	/** Render inside another page surface, without the solution back-link chrome. */
	embedded?: boolean;
}

export function FilesExplorer({
	install,
	installName,
	embedded = false,
}: FilesExplorerProps = {}) {
	const authorization = useAuthorization();
	const explorerRef = useRef<HTMLDivElement>(null);
	const detailTriggerRef = useRef<HTMLElement | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<FileDeleteTarget | null>(
		null,
	);
	const queryClient = useQueryClient();
	function refreshFiles() {
		void queryClient.invalidateQueries({ queryKey: ["file-structure"] });
		void queryClient.invalidateQueries({ queryKey: ["file-shares"] });
		setRefreshKey((key) => key + 1);
	}
	const { data: organizations = [] } = useOrganizations({
		enabled:
			!install &&
			!authorization.isLoading &&
			authorization.canAnywhere("filepolicies.read"),
	});
	const isWide = useMediaQuery("(min-width: 1024px)");
	const isThreePane = useMediaQuery("(min-width: 1280px)");
	const isWideToolWorkspace = useMediaQuery("(min-width: 1440px)");

	// When `install` is set, scope and location are pinned — not user-controlled.
	// Otherwise selectorScope is what OrganizationSelect speaks: null = Global.
	// The data layer needs the EXPLICIT "global" string (not null/UNSET) so
	// write/upload (`resolve_target_org`) and the structural list agree — null
	// would resolve to the caller's own org on the write path while the explorer
	// means literal global.
	const [selectorScope, setSelectorScope] = useState<string | null>(null);
	const canReadGlobal = authorization.canAt(
		"filepolicies.read",
		GLOBAL_TARGET,
	);
	const homeOrganizationId =
		authorization.authorization?.home_organization_id;
	const explicitOrganizationId = authorization.authorization?.grants.find(
		(grant) =>
			grant.permission === "filepolicies.read" &&
			grant.boundary.kind === "organization",
	)?.boundary.organization_id;
	const defaultScope = canReadGlobal
		? null
		: homeOrganizationId &&
			  authorization.canAt(
					"filepolicies.read",
					orgTarget(homeOrganizationId),
			  )
			? homeOrganizationId
			: (explicitOrganizationId ??
				organizations.find((organization) =>
					authorization.canAt(
						"filepolicies.read",
						orgTarget(organization.id),
					),
				)?.id);
	const activeSelectorScope = selectorScope ?? defaultScope;
	const activeTarget =
		activeSelectorScope === null
			? GLOBAL_TARGET
			: activeSelectorScope
				? orgTarget(activeSelectorScope)
				: undefined;
	const scope = activeSelectorScope ?? "global";
	const scopeReady = Boolean(
		authorization.authorization &&
		!authorization.isLoading &&
		(install ||
			(activeTarget &&
				authorization.canAt("filepolicies.read", activeTarget))),
	);
	const policyReadOnly =
		Boolean(install) ||
		!activeTarget ||
		!authorization.canAt("filepolicies.readwrite", activeTarget);
	const [location, setLocation] = useState<string | null>(null);
	const [prefix, setPrefix] = useState("");
	const [selectedFile, setSelectedFile] = useState<string | null>(null);
	const [view, setView] = useState<"browse" | "policies">("browse");

	const [treeOpen, setTreeOpen] = useState(false);
	const [detailOpen, setDetailOpen] = useState(false);
	const [detailTab, setDetailTab] = useState("preview");
	const [newShareOpen, setNewShareOpen] = useState(false);
	const [testOpen, setTestOpen] = useState(false);
	const [policyOpen, setPolicyOpen] = useState(false);
	const [inspectorBusy, setInspectorBusy] = useState(false);
	const returnToDetails = useRef(false);
	// Target of the embedded policy editor or access check.
	const [modalTarget, setModalTarget] = useState<{
		location: string;
		path: string;
		exactPath?: string;
	}>({ location: "", path: "" });
	// Bump to force ShareTree/FolderListing to refetch after a mutation.
	const [refreshKey, setRefreshKey] = useState(0);

	const solutionReadOnly = Boolean(install);
	const readOnly =
		solutionReadOnly ||
		(location !== null && READ_ONLY_LOCATIONS.has(location));
	const canUpload = view === "browse" && location !== null && !readOnly;
	const uploadInputRef = useRef<HTMLInputElement>(null);
	const {
		uploading,
		uploadFiles,
		progress: uploadProgress,
		error: uploadError,
		retryUpload,
	} = useFileUpload(canUpload ? location : null, scope, prefix, refreshFiles);
	const scopeLabel = useMemo(() => {
		if (install) return "Solution";
		if (activeSelectorScope === null) return "Global";
		return (
			organizations.find((o) => o.id === activeSelectorScope)?.name ??
			"Organization"
		);
	}, [install, activeSelectorScope, organizations]);
	const segments = prefix ? prefix.replace(/\/$/, "").split("/") : [];

	function openDetails() {
		detailTriggerRef.current =
			document.activeElement instanceof HTMLElement
				? document.activeElement
				: null;
		setDetailOpen(true);
	}

	function closeDetails() {
		if (inspectorBusy) return;
		setTestOpen(false);
		setPolicyOpen(false);
		// Remove inert before restoring focus to the directory on small screens.
		flushSync(() => setDetailOpen(false));
		const opener = detailTriggerRef.current;
		if (opener?.isConnected && opener !== document.body)
			opener.focus({ preventScroll: true });
		else explorerRef.current?.focus({ preventScroll: true });
	}

	function resetTo(nextLocation: string | null, nextPrefix: string) {
		if (inspectorBusy) return;
		setTestOpen(false);
		setPolicyOpen(false);
		setLocation(nextLocation);
		setPrefix(nextPrefix);
		setSelectedFile(null);
		setDetailTab("access");
		setDetailOpen(false);
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
		returnToDetails.current = detailOpen && !testOpen && !policyOpen;
		setPolicyOpen(false);
		openDetails();
		setModalTarget({ location: loc, path });
		setTestOpen(true);
	}

	function openPolicy(loc: string, path: string, exact = false) {
		if (policyReadOnly) return;
		if (!policyOpen) returnToDetails.current = detailOpen && !testOpen;
		setTestOpen(false);
		openDetails();
		setModalTarget({
			location: loc,
			path,
			exactPath: exact ? path : undefined,
		});
		setPolicyOpen(true);
	}

	function handleDeletePolicy(policy: FilePolicy) {
		if (policyReadOnly) return;
		setDeleteTarget({ kind: "policy", policy });
	}

	function handleTreeAction(
		action: ShareTreeAction,
		loc: string,
		treePrefix: string,
	) {
		if (action === "effective") {
			handleSelect(loc, treePrefix);
			openDetails();
		} else if (action === "test") {
			openTest(loc, treePrefix);
		} else if (action === "newPolicy") {
			if (policyReadOnly) return;
			openPolicy(
				loc,
				treePrefix ? `${treePrefix.replace(/\/+$/, "")}/` : "",
				true,
			);
		} else if (action === "upload") {
			if (READ_ONLY_LOCATIONS.has(loc)) return;
			// Commit the destination before invoking the browser file chooser.
			flushSync(() => {
				setView("browse");
				handleSelect(loc, treePrefix);
			});
			uploadInputRef.current?.click();
		}
	}

	function handleRowAction(action: ListingRowAction, path: string) {
		if (location === null) return;
		if (
			(action === "policy" && policyReadOnly) ||
			(action === "delete" && readOnly)
		)
			return;
		if (action === "preview") {
			setSelectedFile(path);
			setDetailTab("preview");
			openDetails();
		} else if (action === "test") {
			openTest(location, path);
		} else if (action === "policy") {
			openPolicy(location, path);
		} else if (action === "delete") {
			setDeleteTarget({ kind: "file", location, scope, path });
		}
	}

	function selectFile(path: string) {
		setSelectedFile(path);
		setDetailTab("preview");
		openDetails();
	}

	if (!scopeReady) {
		return (
			<div
				role="region"
				aria-label="Files explorer"
				className="flex h-full min-h-0 items-center justify-center rounded-[var(--bf-radius-surface)] border bg-card p-6 text-sm text-muted-foreground"
			>
				{authorization.isLoading
					? "Loading file access…"
					: "You do not have access to files in an available scope."}
			</div>
		);
	}

	const tree = (
		<ShareTree
			key={`tree-${scope}-${install ?? ""}-${refreshKey}`}
			scope={scope}
			solution={install}
			selectedLocation={location}
			selectedPrefix={prefix}
			readOnly={solutionReadOnly}
			policyReadOnly={policyReadOnly}
			onSelect={handleSelect}
			onContextAction={handleTreeAction}
		/>
	);

	const preview = (
		<FilePreview
			location={location ?? ""}
			scope={scope}
			solution={install}
			path={selectedFile}
		/>
	);
	const access = policyReadOnly ? (
		<EffectiveAccessPanel
			key={refreshKey}
			location={location ?? ""}
			scope={scope}
			solution={install}
			path={selectedFile ?? (location ? prefix : null)}
			readOnly={policyReadOnly}
			managedBySolution={solutionReadOnly}
			solutionId={install}
			onOpenTest={() => openTest(location ?? "", selectedFile ?? prefix)}
			onOpenPolicy={
				policyReadOnly
					? undefined
					: (policy) => openPolicy(policy.location, policy.path, true)
			}
			onManagePolicy={() =>
				openPolicy(
					location ?? "",
					selectedFile ??
						(prefix ? `${prefix.replace(/\/+$/, "")}/` : ""),
					true,
				)
			}
		/>
	) : (
		<PolicyEditorPanel
			key={refreshKey}
			location={location ?? ""}
			scope={scope}
			path={
				selectedFile ?? (prefix ? `${prefix.replace(/\/+$/, "")}/` : "")
			}
			onSaved={refreshFiles}
			onOpenSource={(policy) =>
				openPolicy(policy.location, policy.path, true)
			}
			onBusyChange={setInspectorBusy}
		/>
	);

	const detail = (
		<Tabs
			key={selectedFile ?? "access"}
			value={detailTab}
			onValueChange={(tab) => {
				if (!inspectorBusy) setDetailTab(tab);
			}}
			className="flex min-h-0 flex-1 flex-col gap-0"
			data-testid="detail-pane"
		>
			<TabsList
				variant="line"
				aria-label="File details"
				className="mx-4 mt-2 shrink-0"
				inert={inspectorBusy || undefined}
			>
				{selectedFile && (
					<TabsTrigger value="preview" className="min-h-11">
						Preview
					</TabsTrigger>
				)}
				<TabsTrigger value="access" className="min-h-11">
					Access
				</TabsTrigger>
				<TabsTrigger value="test" className="min-h-11">
					Test
				</TabsTrigger>
			</TabsList>
			<TabsContent
				value="preview"
				className="min-h-0 flex-1 overflow-hidden"
			>
				{preview}
			</TabsContent>
			<TabsContent
				value="access"
				className="min-h-0 flex-1 overflow-hidden"
			>
				{access}
			</TabsContent>
			<TabsContent
				value="test"
				className="min-h-0 flex-1 overflow-hidden"
			>
				<TestAccessPanel
					scopeLabel={scopeLabel}
					location={location ?? ""}
					scope={scope}
					solution={install}
					path={selectedFile ?? prefix}
				/>
			</TabsContent>
		</Tabs>
	);
	const solutionTitle = installName ?? "Solution";
	const showTree =
		isWide &&
		(!detailOpen ||
			(isThreePane &&
				((!policyOpen && !testOpen && detailTab === "preview") ||
					isWideToolWorkspace)));
	const toolOpen = testOpen || policyOpen;
	function closeTool() {
		setInspectorBusy(false);
		setTestOpen(false);
		setPolicyOpen(false);
		if (!returnToDetails.current) {
			setDetailOpen(false);
			requestAnimationFrame(() =>
				detailTriggerRef.current?.focus({ preventScroll: true }),
			);
		}
	}
	const inspectorTitle = toolOpen
		? (modalTarget.path.split("/").filter(Boolean).at(-1) ??
			modalTarget.location)
		: (selectedFile?.split("/").pop() ??
			segments.at(-1) ??
			location ??
			"Details");
	const inspectorPath = toolOpen
		? `${modalTarget.location}/${modalTarget.path}`
		: `${location ?? ""}/${selectedFile ?? prefix}`;
	const toolContent = policyOpen ? (
		<PolicyEditorPanel
			location={modalTarget.location}
			scope={scope}
			path={modalTarget.path}
			exactPath={modalTarget.exactPath}
			onOpenChange={closeTool}
			onSaved={refreshFiles}
			onOpenSource={(policy) =>
				openPolicy(policy.location, policy.path, true)
			}
			onBusyChange={setInspectorBusy}
		/>
	) : testOpen ? (
		<TestAccessPanel
			scopeLabel={scopeLabel}
			location={modalTarget.location}
			scope={scope}
			solution={install}
			path={modalTarget.path}
		/>
	) : (
		detail
	);

	const inspectorContent = toolOpen ? (
		<Tabs
			value={policyOpen ? "access" : "test"}
			onValueChange={(tab) => {
				if (inspectorBusy) return;
				setPolicyOpen(tab === "access");
				setTestOpen(tab === "test");
			}}
			className="flex min-h-0 flex-1 flex-col gap-0"
		>
			<TabsList
				variant="line"
				aria-label="File details"
				className="mx-4 mt-2 shrink-0"
				inert={inspectorBusy || undefined}
			>
				<TabsTrigger
					value="access"
					disabled={policyReadOnly}
					className="min-h-11"
				>
					Access
				</TabsTrigger>
				<TabsTrigger value="test" className="min-h-11">
					Test
				</TabsTrigger>
			</TabsList>
			<TabsContent
				value={policyOpen ? "access" : "test"}
				className="flex min-h-0 flex-1 flex-col overflow-hidden"
			>
				{toolContent}
			</TabsContent>
		</Tabs>
	) : (
		detail
	);

	return (
		<div
			ref={explorerRef}
			tabIndex={-1}
			role="region"
			aria-label="Files explorer"
			className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card outline-none"
		>
			<header
				inert={inspectorBusy || undefined}
				className="flex shrink-0 flex-wrap items-center border-b bg-muted/20"
			>
				{install && !embedded && (
					<div className="flex min-w-0 flex-1 items-center gap-2 px-4 py-2">
						<Button
							asChild
							variant="ghost"
							size="icon"
							aria-label="Back to Solution"
						>
							<Link
								to={`/solutions/${install}`}
								data-testid="files-solution-back"
							>
								<ChevronLeft className="size-4" />
							</Link>
						</Button>
						<span
							className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]"
							title={solutionTitle}
						>
							{solutionTitle}
						</span>
					</div>
				)}
				{!install && (
					<div className="flex min-w-0 w-full items-stretch border-b sm:w-[17rem] sm:shrink-0 sm:self-stretch sm:border-b-0 sm:border-r">
						<OrganizationSelect
							aria-label="File scope"
							triggerClassName="h-full min-h-14 rounded-none border-0 bg-transparent px-4 py-3 shadow-none hover:bg-muted/50 focus-visible:ring-inset lg:min-h-14"
							value={activeSelectorScope}
							onChange={handleScopeChange}
							showGlobal={canReadGlobal}
							showAll={false}
							filterOrganizations={(organization) =>
								authorization.canAt(
									"filepolicies.read",
									orgTarget(organization.id),
								)
							}
						/>
					</div>
				)}
				{!install && (
					<div className="flex min-w-0 flex-1 basis-64 items-stretch gap-3 pl-4">
						<Tabs
							value={view}
							onValueChange={(value) => {
								setView(value as "browse" | "policies");
								setDetailOpen(false);
								setTestOpen(false);
								setPolicyOpen(false);
							}}
							className="flex min-w-0 items-center py-2"
						>
							<TabsList
								variant="line"
								aria-label="Files workspace"
							>
								<TabsTrigger
									value="browse"
									className="min-h-11"
								>
									<FolderOpen className="size-4" />
									Files
								</TabsTrigger>
								<TabsTrigger
									value="policies"
									className="min-h-11"
								>
									<ShieldCheck className="size-4" />
									Access Policies
								</TabsTrigger>
							</TabsList>
						</Tabs>
						{!policyReadOnly && (
							<WorkspacePrimaryAction
								type="button"
								aria-label="New Share"
								className="ml-auto"
								onClick={() => setNewShareOpen(true)}
							>
								<Plus className="size-4" />
								<span className="hidden sm:inline">
									New Share
								</span>
							</WorkspacePrimaryAction>
						)}
					</div>
				)}
			</header>
			<div className="relative flex min-h-0 flex-1 overflow-hidden">
				{showTree && (
					<aside
						aria-label="Share navigation"
						inert={inspectorBusy || undefined}
						className="flex w-[17rem] shrink-0 flex-col border-r bg-muted/10"
					>
						<div className="flex h-14 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold">
							<HardDrive className="size-4 text-primary" />
							Shares
						</div>
						{tree}
					</aside>
				)}
				<div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
					<div
						inert={
							inspectorBusy || (!isWide && detailOpen)
								? true
								: undefined
						}
						className="flex min-h-0 flex-1 flex-col"
					>
						{
							<div className="flex min-h-14 shrink-0 flex-wrap items-stretch gap-2 border-b pl-3 sm:pl-4 lg:h-14 lg:flex-nowrap">
								{!showTree && (
									<Sheet
										open={treeOpen}
										onOpenChange={setTreeOpen}
									>
										<SheetTrigger asChild>
											<Button
												variant="outline"
												size="icon"
												aria-label="Open shares"
												className="my-2"
											>
												<Menu className="size-4" />
											</Button>
										</SheetTrigger>
										<SheetContent
											side="left"
											className="w-72 p-0"
										>
											<SheetHeader>
												<SheetTitle>Shares</SheetTitle>
											</SheetHeader>
											<div className="flex min-h-0 flex-1 flex-col">
												{tree}
											</div>
										</SheetContent>
									</Sheet>
								)}
								<div className="flex min-w-0 flex-1 items-center py-2">
									<Breadcrumbs
										scopeLabel={
											install ? solutionTitle : scopeLabel
										}
										includeScopeRoot={!install}
										location={location}
										segments={segments}
										onNavigate={handleBreadcrumb}
									/>
								</div>
								{location !== null && (
									<Button
										aria-label="Folder Details"
										variant="ghost"
										size="sm"
										className="my-2 min-h-10"
										onClick={() => {
											setSelectedFile(null);
											setDetailTab("access");
											openDetails();
										}}
									>
										<Info className="size-4" />
										<span className="hidden xl:inline">
											Folder Details
										</span>
									</Button>
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
													void uploadFiles(
														event.target.files,
													);
												event.target.value = "";
											}}
										/>
										<WorkspacePrimaryAction
											onClick={() =>
												uploadInputRef.current?.click()
											}
											disabled={uploading}
										>
											<Upload className="size-4" />
											{uploading
												? "Uploading…"
												: "Upload"}
										</WorkspacePrimaryAction>
									</>
								)}
							</div>
						}
						{uploadProgress && (
							<p
								role="status"
								className="border-b px-4 py-2 text-sm text-muted-foreground [overflow-wrap:anywhere]"
							>
								{uploadProgress}
							</p>
						)}
						{uploadError && (
							<Alert variant="destructive" className="m-3 w-auto">
								<AlertTitle>Upload failed</AlertTitle>
								<AlertDescription>
									<p>{uploadError}</p>
									<Button
										variant="outline"
										className="mt-2"
										disabled={uploading}
										onClick={() => void retryUpload()}
									>
										Retry upload
									</Button>
								</AlertDescription>
							</Alert>
						)}
						{view === "policies" && !install ? (
							<PoliciesView
								scope={scope}
								readOnly={policyReadOnly}
								refreshKey={refreshKey}
								location={location}
								prefix={prefix}
								onEdit={(policy) =>
									openPolicy(
										policy.location,
										policy.path,
										true,
									)
								}
								onDelete={handleDeletePolicy}
							/>
						) : location === null ? (
							<SharesOverview
								scope={scope}
								solution={install}
								readOnly={solutionReadOnly}
								onSelect={handleSelect}
							/>
						) : (
							<FolderListing
								key={`listing-${scope}-${install ?? ""}-${location}-${prefix}`}
								scope={scope}
								solution={install}
								location={location}
								prefix={prefix}
								readOnly={readOnly}
								policyReadOnly={policyReadOnly}
								managedBySolution={solutionReadOnly}
								solutionId={install}
								selectedPath={detailOpen ? selectedFile : null}
								onOpenFolder={(next) => resetTo(location, next)}
								onSelectFile={selectFile}
								onRowAction={handleRowAction}
								onFolderAction={(action, folderPrefix) =>
									handleTreeAction(
										action,
										location,
										folderPrefix,
									)
								}
								onUploaded={refreshFiles}
							/>
						)}
					</div>
					{!isWide && (
						<AnimatePresence initial={false}>
							{detailOpen && (
								<FilesInspector
									key="file-inspector"
									inline={false}
									title={inspectorTitle}
									path={inspectorPath}
									isFile={Boolean(selectedFile)}
									onClose={closeDetails}
									busy={inspectorBusy}
									width={
										(toolOpen || detailTab !== "preview") &&
										isThreePane
											? 480
											: 384
									}
								>
									{inspectorContent}
								</FilesInspector>
							)}
						</AnimatePresence>
					)}
				</div>
				{isWide && (
					<AnimatePresence initial={false}>
						{detailOpen && (
							<FilesInspector
								key="file-inspector"
								inline
								title={inspectorTitle}
								path={inspectorPath}
								isFile={Boolean(selectedFile)}
								onClose={closeDetails}
								busy={inspectorBusy}
								width={
									(toolOpen || detailTab !== "preview") &&
									isThreePane
										? 480
										: 384
								}
							>
								{inspectorContent}
							</FilesInspector>
						)}
					</AnimatePresence>
				)}
			</div>

			{deleteTarget && (
				<DeleteConfirmation
					target={deleteTarget}
					onClose={() => setDeleteTarget(null)}
					onRestoreFocus={() => {
						explorerRef.current?.focus({ preventScroll: true });
					}}
					onDeleted={(target) => {
						if (
							target.kind === "file" &&
							target.location === location &&
							target.scope === scope &&
							target.path === selectedFile
						) {
							setSelectedFile(null);
							setDetailOpen(false);
						}
						toast.success(
							target.kind === "file"
								? "File deleted"
								: "File policy deleted",
						);
						refreshFiles();
					}}
				/>
			)}

			<NewShareDialog
				open={newShareOpen}
				onOpenChange={setNewShareOpen}
				scope={scope}
				onCreated={(loc) => {
					refreshFiles();
					setView("browse");
					handleSelect(loc, "");
				}}
			/>
		</div>
	);
}
