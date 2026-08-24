/**
 * Knowledge Document Drawer
 *
 * Sheet component for editing and creating documents.
 * Uses the TiptapEditor for rich markdown editing.
 * Documents open read-only unless the selected authorization context may edit
 * that exact resource boundary.
 */

import { useState, useEffect, useCallback } from "react";
import { Save, X, ChevronDown, ChevronRight } from "lucide-react";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";

interface KnowledgeDocumentDrawerProps {
	namespace: string;
	documentId: string | null;
	isCreating: boolean;
	canEdit: boolean;
	selectedOrganizationId: string | null;
	onClose: () => void;
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
		<Collapsible open={open} onOpenChange={setOpen}>
			<CollapsibleTrigger className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
				{open ? (
					<ChevronDown className="h-4 w-4" />
				) : (
					<ChevronRight className="h-4 w-4" />
				)}
				Metadata
			</CollapsibleTrigger>
			<CollapsibleContent className="mt-2">
				<VariablesTreeView data={metadata} />
			</CollapsibleContent>
		</Collapsible>
	);
}

export function KnowledgeDocumentDrawer({
	namespace,
	documentId,
	isCreating,
	canEdit,
	selectedOrganizationId,
	onClose,
}: KnowledgeDocumentDrawerProps) {
	const [document, setDocument] = useState<DocumentFull | null>(null);
	const [content, setContent] = useState("");
	const [key, setKey] = useState("");
	const [createNamespace, setCreateNamespace] = useState("");
	const [isSaving, setIsSaving] = useState(false);

	const isOpen = !!documentId || isCreating;

	const loadDocument = useCallback(async () => {
		if (!documentId || !namespace) return;
		try {
			const response = await authFetch(
				`/api/knowledge-sources/${encodeURIComponent(namespace)}/documents/${documentId}`,
			);
			if (response.ok) {
				const data: DocumentFull = await response.json();
				setDocument(data);
				setContent(data.content);
				setKey(data.key || "");
			}
		} catch {
			toast.error("Failed to load document");
		}
	}, [documentId, namespace]);

	// Either load the existing document (async) or reset form for creation
	// (sync). The reset path is wrapped in a microtask so the synchronous
	// effect body does not directly invoke setState (set-state-in-effect rule).
	useEffect(() => {
		if (documentId) {
			void (async () => {
				await loadDocument();
			})();
		} else if (isCreating) {
			queueMicrotask(() => {
				setDocument(null);
				setContent("");
				setKey("");
				setCreateNamespace("");
			});
		}
	}, [documentId, isCreating, loadDocument]);

	const canEditDocument =
		canEdit &&
		(isCreating || document?.organization_id === selectedOrganizationId);

	const handleSave = async () => {
		if (!canEditDocument) return;
		if (!content.trim()) {
			toast.error("Content is required");
			return;
		}

		setIsSaving(true);
		try {
			if (isCreating) {
				const ns = createNamespace.trim();
				if (!ns) {
					toast.error("Namespace is required");
					setIsSaving(false);
					return;
				}
				const response = await authFetch(
					`/api/knowledge-sources/${encodeURIComponent(ns)}/documents`,
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
					toast.success("Document created");
					onClose();
				} else if (response.status === 409) {
					const err = await response.json().catch(() => ({}));
					const detail = err.detail;
					const msg =
						typeof detail === "object"
							? detail?.message
							: detail || "Resource already exists";
					toast.error(msg);
				} else {
					const err = await response.json().catch(() => ({}));
					toast.error(err.detail || "Failed to create document");
				}
			} else if (documentId && namespace) {
				const response = await authFetch(
					`/api/knowledge-sources/${encodeURIComponent(namespace)}/documents/${documentId}`,
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
					toast.success("Document updated");
					onClose();
				} else {
					const err = await response.json().catch(() => ({}));
					toast.error(err.detail || "Failed to update document");
				}
			}
		} catch {
			toast.error("Failed to save document");
		} finally {
			setIsSaving(false);
		}
	};

	return (
		<Sheet open={isOpen} onOpenChange={() => onClose()}>
			<SheetContent className="sm:max-w-[800px] flex flex-col">
				<SheetHeader>
					<SheetTitle>
						{isCreating
							? "New Document"
							: document?.key || "Document"}
					</SheetTitle>
				</SheetHeader>

				<div className="flex-1 flex flex-col gap-4 overflow-hidden mt-4 px-6">
					{/* Create-mode fields */}
					{isCreating && (
						<>
							<div className="space-y-2">
								<Label htmlFor="doc-namespace">Namespace</Label>
								<Input
									id="doc-namespace"
									value={createNamespace}
									onChange={(e) =>
										setCreateNamespace(e.target.value)
									}
									placeholder="e.g. company-docs"
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="doc-key">Key (optional)</Label>
								<Input
									id="doc-key"
									value={key}
									onChange={(e) => setKey(e.target.value)}
									placeholder="unique-document-key"
								/>
							</div>
						</>
					)}

					{/* Editor */}
					<div className="flex-1 min-h-0 overflow-hidden rounded-md ring-1 ring-foreground/5">
						<TiptapEditor
							content={content}
							onChange={setContent}
							readOnly={!canEditDocument}
							className="h-full border-0 rounded-none"
						/>
					</div>

					{/* Metadata (view mode only) */}
					{!isCreating &&
						document &&
						Object.keys(document.metadata).length > 0 && (
							<MetadataSection metadata={document.metadata} />
						)}

					{/* Actions */}
					<div className="flex justify-end gap-2 py-2 pb-6">
						<Button
							variant="outline"
							onClick={() => {
								if (!isCreating && document) {
									setContent(document.content || "");
								}
								onClose();
							}}
						>
							<X className="h-4 w-4 mr-1" />
							{canEditDocument ? "Cancel" : "Close"}
						</Button>
						{canEditDocument && (
							<Button onClick={handleSave} disabled={isSaving}>
								<Save className="h-4 w-4 mr-1" />
								{isSaving ? "Saving..." : "Save"}
							</Button>
						)}
					</div>
				</div>
			</SheetContent>
		</Sheet>
	);
}
