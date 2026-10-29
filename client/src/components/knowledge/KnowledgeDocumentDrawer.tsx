/**
 * Knowledge Document Drawer
 *
 * Sheet component for editing and creating documents.
 * Uses the TiptapEditor for rich markdown editing.
 * Documents always open in editable mode.
 */

import {
	useState,
	useEffect,
	useCallback,
	useRef,
	useId,
	type RefObject,
	type ReactNode,
} from "react";
import {
	Save,
	X,
	ChevronDown,
	ChevronRight,
	Loader2,
	BookOpen,
} from "lucide-react";
import {
	Sheet,
	SheetContent,
	SheetTitle,
	SheetDescription,
} from "@/components/ui/sheet";
import {
	AlertDialog,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";

const DOCUMENT_SAVED_TOAST = "knowledge-document-saved";

interface KnowledgeDocumentDrawerProps {
	namespace: string;
	documentId: string | null;
	isCreating: boolean;
	onClose: () => void;
	returnFocusRef?: RefObject<HTMLElement | null>;
	embedded?: boolean;
	onBusyChange?: (busy: boolean) => void;
}

interface DocumentFull {
	id: string;
	namespace: string;
	key: string | null;
	content: string;
	metadata: Record<string, unknown>;
	organization_id: string | null;
	created_at: string | null;
	updated_at: string | null;
}

function MetadataSection({ metadata }: { metadata: Record<string, unknown> }) {
	const [open, setOpen] = useState(false);
	return (
		<Collapsible open={open} onOpenChange={setOpen} className="shrink-0">
			<CollapsibleTrigger className="flex min-h-11 items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none">
				{open ? (
					<ChevronDown className="h-4 w-4" />
				) : (
					<ChevronRight className="h-4 w-4" />
				)}
				Metadata
			</CollapsibleTrigger>
			<CollapsibleContent className="mt-2 max-h-[40dvh] overflow-auto">
				<VariablesTreeView data={metadata} />
			</CollapsibleContent>
		</Collapsible>
	);
}

export function KnowledgeDocumentDrawer(props: KnowledgeDocumentDrawerProps) {
	if (!props.documentId && !props.isCreating) return null;
	return (
		<KnowledgeDocumentSession
			key={JSON.stringify([
				props.isCreating,
				props.namespace,
				props.documentId,
			])}
			{...props}
		/>
	);
}

function responseMessage(detail: unknown, fallback: string): string {
	if (typeof detail === "string") return detail;
	if (
		detail &&
		typeof detail === "object" &&
		"message" in detail &&
		typeof detail.message === "string"
	)
		return detail.message;
	return fallback;
}

function KnowledgeDocumentEditorFrame({
	title,
	description,
	isSaving,
	onClose,
	headerId,
	descriptionId,
	embedded,
	children,
}: {
	title: string;
	description: string;
	isSaving: boolean;
	onClose: () => void;
	headerId: string;
	descriptionId: string;
	embedded: boolean;
	children: ReactNode;
}) {
	return (
		<>
			<header
				className={
					embedded
						? "shrink-0 border-b bg-muted/20 px-4 py-3"
						: "shrink-0 border-b px-4 py-5 pr-16 sm:px-6 sm:pr-16"
				}
			>
				<div className="flex min-w-0 items-start justify-between gap-3">
					{embedded && (
						<BookOpen
							aria-hidden="true"
							className="mt-1 size-5 shrink-0 text-primary"
						/>
					)}
					<div className="min-w-0 flex-1">
						{embedded ? (
							<h2
								id={headerId}
								className="text-sm font-semibold leading-6 [overflow-wrap:anywhere]"
							>
								{title}
							</h2>
						) : (
							<SheetTitle
								id={headerId}
								className="leading-6 [overflow-wrap:anywhere]"
							>
								{title}
							</SheetTitle>
						)}
						{embedded ? (
							<p
								id={descriptionId}
								className="mt-1 text-sm text-muted-foreground"
							>
								{description}
							</p>
						) : (
							<SheetDescription id={descriptionId}>
								{description}
							</SheetDescription>
						)}
					</div>
					{embedded && (
						<Button
							variant="ghost"
							size="icon"
							className="shrink-0"
							disabled={isSaving}
							onClick={onClose}
							aria-label="Close document"
						>
							<X className="size-4" />
						</Button>
					)}
				</div>
			</header>
			{children}
		</>
	);
}

function KnowledgeDocumentSession({
	namespace,
	documentId,
	isCreating,
	onClose,
	returnFocusRef,
	embedded = false,
	onBusyChange,
}: KnowledgeDocumentDrawerProps) {
	const returnFocus = useDialogReturnFocus(returnFocusRef, true);
	const editorId = useId();
	const { user } = useAuth();
	const authorization = useAuthorization();
	const [document, setDocument] = useState<DocumentFull | null>(null);
	const [content, setContent] = useState("");
	const [key, setKey] = useState("");
	const [createNamespace, setCreateNamespace] = useState("");
	const [scopeOrgId, setScopeOrgId] = useState<string | null | undefined>(
		user?.organizationId ?? null,
	);
	const saveBusy = useRef(false);
	const saveErrorRef = useRef<HTMLDivElement>(null);
	const [isSaving, setIsSaving] = useState(false);
	const [conflictMessage, setConflictMessage] = useState<string | null>(null);

	const [isLoading, setIsLoading] = useState(!!documentId);
	const [loadError, setLoadError] = useState(false);
	const [saveError, setSaveError] = useState<string | null>(null);
	const loadController = useRef<AbortController | null>(null);
	const currentOrganizationId = document?.organization_id ?? scopeOrgId;
	const canManageCurrentScope = authorization.canAt(
		"knowledge.readwrite",
		orgTarget(currentOrganizationId),
	);
	const canManageSelectedScope = authorization.canAt(
		"knowledge.readwrite",
		orgTarget(scopeOrgId),
	);
	const canChooseScope = authorization.canAnywhere("knowledge.readwrite");
	const canSave = isCreating
		? canManageSelectedScope
		: canManageCurrentScope && canManageSelectedScope;
	const knownOrganizations = [
		...(user?.organizationId
			? [{ id: user.organizationId, name: "Your organization" }]
			: []),
		...(document?.organization_id
			? [
					{
						id: document.organization_id,
						name: "Document organization",
					},
				]
			: []),
	];

	useEffect(() => {
		if (saveError) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [saveError]);

	useEffect(() => {
		onBusyChange?.(isSaving);
	}, [isSaving, onBusyChange]);

	const loadDocument = useCallback(async () => {
		if (!documentId || !namespace) return;
		loadController.current?.abort();
		const controller = new AbortController();
		loadController.current = controller;
		try {
			const response = await authFetch(
				`/api/knowledge-sources/${encodeURIComponent(namespace)}/documents/${documentId}`,
				{ signal: controller.signal },
			);
			if (!response.ok) throw new Error("Failed to load document");
			const data: DocumentFull = await response.json();
			if (controller.signal.aborted) return;
			setDocument(data);
			setContent(data.content);
			setKey(data.key || "");
			setScopeOrgId(data.organization_id ?? null);
		} catch {
			if (!controller.signal.aborted) setLoadError(true);
		} finally {
			if (!controller.signal.aborted) setIsLoading(false);
		}
	}, [documentId, namespace]);

	useEffect(() => {
		toast.dismiss(DOCUMENT_SAVED_TOAST);
		void (async () => {
			await loadDocument();
		})();
		return () => loadController.current?.abort();
	}, [loadDocument]);

	const handleSave = async (forceReplace = false) => {
		if (saveBusy.current || isLoading || loadError || !canSave) return;
		setSaveError(null);
		if (!content.trim()) {
			setSaveError("Content is required");
			return;
		}

		saveBusy.current = true;
		setIsSaving(true);
		try {
			if (isCreating) {
				const ns = createNamespace.trim();
				if (!ns) {
					setSaveError("Namespace is required");
					setIsSaving(false);
					return;
				}
				const params = new URLSearchParams();
				if (scopeOrgId === null) {
					params.set("scope", "global");
				} else if (scopeOrgId) {
					params.set("scope", scopeOrgId);
				}
				const qs = params.toString();
				const response = await authFetch(
					`/api/knowledge-sources/${encodeURIComponent(ns)}/documents${qs ? `?${qs}` : ""}`,
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							content: content.trim(),
							key: key.trim() || null,
							metadata: {},
						}),
					},
				);
				if (response.ok) {
					toast.success("Document created", {
						id: DOCUMENT_SAVED_TOAST,
					});
					onClose();
				} else if (response.status === 409) {
					const err = await response.json().catch(() => ({}));
					const msg = responseMessage(
						err.detail,
						"Resource already exists",
					);
					setSaveError(msg);
				} else {
					const err = await response.json().catch(() => ({}));
					setSaveError(
						responseMessage(
							err.detail,
							"Failed to create document",
						),
					);
				}
			} else if (documentId && namespace) {
				const params = new URLSearchParams();
				if (scopeOrgId === null) {
					params.set("scope", "global");
				} else if (scopeOrgId) {
					params.set("scope", scopeOrgId);
				}
				if (forceReplace) {
					params.set("replace", "true");
				}
				const qs = params.toString();
				const response = await authFetch(
					`/api/knowledge-sources/${encodeURIComponent(namespace)}/documents/${documentId}${qs ? `?${qs}` : ""}`,
					{
						method: "PUT",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							content: content.trim(),
							metadata: document?.metadata || {},
						}),
					},
				);
				if (response.ok) {
					toast.success("Document updated", {
						id: DOCUMENT_SAVED_TOAST,
					});
					onClose();
				} else if (response.status === 409) {
					const err = await response.json().catch(() => ({}));
					const msg = responseMessage(
						err.detail,
						"Resource already exists",
					);
					setConflictMessage(msg);
				} else {
					const err = await response.json().catch(() => ({}));
					setSaveError(
						responseMessage(
							err.detail,
							"Failed to update document",
						),
					);
				}
			}
		} catch {
			setSaveError("Failed to save document");
		} finally {
			saveBusy.current = false;
			setIsSaving(false);
		}
	};

	const title = isCreating ? "New Document" : document?.key || "Document";
	const description = isCreating
		? "Add a reference for your agents."
		: namespace;

	const editorFrame = (
		<KnowledgeDocumentEditorFrame
			title={title}
			description={description}
			isSaving={isSaving}
			onClose={onClose}
			headerId={`${editorId}-title`}
			descriptionId={`${editorId}-description`}
			embedded={embedded}
		>
			{isLoading ? (
				<div role="status" className="p-6 text-muted-foreground">
					Loading document…
				</div>
			) : loadError ? (
				<Alert variant="destructive" className="m-4 w-auto">
					<AlertTitle>Document could not be loaded</AlertTitle>
					<AlertDescription>
						<Button
							className="mt-3 min-h-11"
							variant="outline"
							onClick={() => {
								setIsLoading(true);
								setLoadError(false);
								void loadDocument();
							}}
						>
							Retry document
						</Button>
					</AlertDescription>
				</Alert>
			) : (
				<div
					role="region"
					aria-label="Document settings"
					className={`flex-1 min-h-0 overflow-y-auto px-4 py-4 sm:px-6 ${embedded ? "lg:flex lg:flex-col" : ""}`}
				>
					<fieldset
						disabled={isSaving || !canSave}
						className={`flex min-w-0 flex-col gap-4 ${embedded ? "lg:min-h-0 lg:flex-1" : ""}`}
					>
						{canChooseScope && (
							<div className="space-y-2">
								<Label htmlFor="knowledge-scope">
									Organization
								</Label>
								<OrganizationSelect
									id="knowledge-scope"
									disabled={isSaving}
									value={scopeOrgId}
									onChange={setScopeOrgId}
									showGlobal={true}
									knownOrganizations={knownOrganizations}
									loadOrganizations={authorization.canAnywhere(
										"organizations.read",
									)}
								/>
							</div>
						)}

						{/* Create-mode fields */}
						{isCreating && (
							<>
								<div className="space-y-2">
									<Label htmlFor="doc-namespace">
										Namespace
									</Label>
									<Input
										className="min-h-11"
										id="doc-namespace"
										value={createNamespace}
										onChange={(e) =>
											setCreateNamespace(e.target.value)
										}
										placeholder="e.g. company-docs"
									/>
								</div>
								<div className="space-y-2">
									<Label htmlFor="doc-key">
										Key (optional)
									</Label>
									<Input
										className="min-h-11"
										id="doc-key"
										value={key}
										onChange={(e) => setKey(e.target.value)}
										placeholder="unique-document-key"
									/>
								</div>
							</>
						)}

						{/* Editor */}
						<div
							className={`min-h-[380px] h-[50dvh] shrink-0 overflow-hidden rounded-[var(--bf-radius-control)] border border-border ${embedded ? "lg:min-h-40 lg:h-auto lg:flex-1 lg:shrink" : ""}`}
						>
							<TiptapEditor
								ariaLabel="Document content"
								readOnly={isSaving || !canSave}
								content={content}
								onChange={setContent}
								className="h-full border-0 rounded-none"
							/>
						</div>

						{/* Metadata (view mode only) */}
						{!isCreating &&
							document &&
							Object.keys(document.metadata).length > 0 && (
								<MetadataSection metadata={document.metadata} />
							)}
					</fieldset>
				</div>
			)}
			{saveError && !conflictMessage && (
				<Alert
					variant="destructive"
					ref={saveErrorRef}
					tabIndex={-1}
					className="outline-none mx-4 mb-3 w-auto shrink-0 max-h-32 overflow-y-auto"
				>
					<AlertTitle>Document could not be saved</AlertTitle>
					<AlertDescription>{saveError}</AlertDescription>
				</Alert>
			)}
			<div className="flex shrink-0 justify-end gap-2 border-t px-4 py-4 sm:px-6">
				<Button
					variant="outline"
					className="min-h-11"
					disabled={isSaving}
					onClick={onClose}
				>
					<X className="size-4" />
					Cancel
				</Button>
				<Button
					className="min-h-11"
					onClick={() => void handleSave(false)}
					disabled={isSaving || isLoading || loadError || !canSave}
				>
					{isSaving ? (
						<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
					) : (
						<Save className="size-4" />
					)}
					{isSaving ? "Saving…" : "Save"}
				</Button>
			</div>
		</KnowledgeDocumentEditorFrame>
	);

	return (
		<>
			{embedded ? (
				<div
					role="region"
					className="flex min-h-0 flex-1 flex-col overflow-hidden"
					aria-labelledby={`${editorId}-title`}
					aria-describedby={`${editorId}-description`}
				>
					{editorFrame}
				</div>
			) : (
				<Sheet
					open
					onOpenChange={(open) => {
						if (!open && !isSaving) onClose();
					}}
				>
					<SheetContent
						{...returnFocus}
						className="w-full sm:max-w-[800px] h-dvh flex flex-col overflow-hidden"
						showCloseButton={!isSaving}
						onEscapeKeyDown={(event) => {
							if (isSaving) event.preventDefault();
						}}
						onInteractOutside={(event) => {
							if (isSaving) event.preventDefault();
						}}
					>
						{editorFrame}
					</SheetContent>
				</Sheet>
			)}

			{/* Replace confirmation dialog */}
			<AlertDialog
				open={!!conflictMessage}
				onOpenChange={(open) => {
					if (!open && !saveBusy.current) {
						setConflictMessage(null);
						setSaveError(null);
					}
				}}
			>
				<AlertDialogContent
					className="max-h-[90dvh] overflow-y-auto [overflow-wrap:anywhere]"
					onEscapeKeyDown={(e) => {
						if (saveBusy.current) e.preventDefault();
					}}
				>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Replace Existing Document?
						</AlertDialogTitle>
						<AlertDialogDescription>
							{conflictMessage} Do you want to replace it?
						</AlertDialogDescription>
					</AlertDialogHeader>
					{saveError && (
						<Alert
							ref={saveErrorRef}
							tabIndex={-1}
							variant="destructive"
							className="outline-none"
						>
							<AlertTitle>
								Document could not be replaced
							</AlertTitle>
							<AlertDescription>{saveError}</AlertDescription>
						</Alert>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel
							disabled={isSaving || !canSave}
							className="min-h-11"
						>
							Cancel
						</AlertDialogCancel>
						<Button
							variant="destructive"
							disabled={isSaving}
							className="min-h-11"
							onClick={() => void handleSave(true)}
						>
							{isSaving ? "Replacing…" : "Replace"}
						</Button>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
