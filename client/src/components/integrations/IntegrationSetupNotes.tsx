import { lazy, Suspense, useState, type ReactNode } from "react";
import { BookOpen, ChevronDown, ChevronUp, Pencil, Plus } from "lucide-react";
import {
	MarkdownContent,
	type MarkdownContentProps,
} from "@/components/common/MarkdownContent";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import "./IntegrationSetupNotes.css";

const TiptapEditor = lazy(async () => ({
	default: (await import("@/components/ui/tiptap-editor")).TiptapEditor,
}));

const notesMarkdownComponents: MarkdownContentProps["components"] = {
	a: ({ children, href }) => (
		<a
			href={href}
			target="_blank"
			rel="noopener noreferrer"
			className="text-primary underline underline-offset-2"
		>
			{children}
		</a>
	),
};

export function IntegrationSetupNotes({
	notes,
	canEdit = false,
	onEdit,
}: {
	notes?: string | null;
	canEdit?: boolean;
	onEdit?: () => void;
}) {
	const [expanded, setExpanded] = useState(true);
	if (!notes) {
		return canEdit && onEdit ? (
			<section
				className="integration-setup-notes-empty"
				aria-label="Setup instructions"
			>
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					onClick={onEdit}
				>
					<Plus className="size-4" />
					Add Setup Instructions
				</Button>
			</section>
		) : null;
	}

	return (
		<section
			className="integration-setup-notes"
			aria-label="Setup instructions"
		>
			<div className="flex items-center gap-2 border-b p-3">
				<BookOpen className="size-4 shrink-0 text-primary" />
				<h2 className="min-w-0 flex-1 text-sm font-semibold">
					Setup Instructions
				</h2>
				{canEdit && onEdit ? (
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label="Edit setup instructions"
						onClick={onEdit}
					>
						<Pencil className="size-4" />
					</Button>
				) : null}
				<Button
					type="button"
					variant="ghost"
					size="icon"
					aria-label={
						expanded
							? "Collapse setup instructions"
							: "Expand setup instructions"
					}
					aria-expanded={expanded}
					onClick={() => setExpanded((value) => !value)}
				>
					{expanded ? (
						<ChevronUp className="size-4" />
					) : (
						<ChevronDown className="size-4" />
					)}
				</Button>
			</div>
			<div
				hidden={!expanded}
				className="integration-setup-notes-content p-4"
			>
				<MarkdownContent
					content={notes}
					components={notesMarkdownComponents}
				/>
			</div>
		</section>
	);
}

export function IntegrationSetupNotesContext({
	notes,
	canEdit = false,
	onEdit,
	children,
}: {
	notes?: string | null;
	canEdit?: boolean;
	onEdit?: () => void;
	children: ReactNode;
}) {
	const showReference = Boolean(notes || (canEdit && onEdit));
	return (
		<div className="integration-setup-notes-container">
			<div
				className="integration-setup-notes-context"
				data-has-notes={Boolean(notes)}
			>
				{showReference && (
					<aside>
						<IntegrationSetupNotes
							notes={notes}
							canEdit={canEdit}
							onEdit={onEdit}
						/>
					</aside>
				)}
				<div className="integration-setup-notes-work min-w-0 space-y-6">
					{children}
				</div>
			</div>
		</div>
	);
}

interface IntegrationSetupNotesDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	notes?: string | null;
	onSave: (notes: string | null) => Promise<void>;
	isSaving?: boolean;
}

function IntegrationSetupNotesEditor({
	notes,
	onSave,
	isSaving = false,
	onCancel,
}: Omit<IntegrationSetupNotesDialogProps, "open" | "onOpenChange"> & {
	onCancel: () => void;
}) {
	const [draft, setDraft] = useState(notes ?? "");
	const [saveError, setSaveError] = useState<string | null>(null);
	return (
		<>
			<DialogHeader className="shrink-0">
				<DialogTitle>Edit Setup Instructions</DialogTitle>
				<DialogDescription>
					Keep setup steps, links, and runbooks with this integration.
					Do not include credentials.
				</DialogDescription>
			</DialogHeader>
			<Suspense
				fallback={
					<div className="min-h-[260px] animate-pulse motion-reduce:animate-none rounded-[var(--bf-radius-surface)] border bg-muted/50" />
				}
			>
				<TiptapEditor
					content={draft}
					onChange={setDraft}
					ariaLabel="Setup instructions"
					className="min-h-0 max-h-[58dvh]"
					editorClassName="min-h-[260px]"
				/>
			</Suspense>
			<DialogFooter className="shrink-0">
				<Button
					type="button"
					variant="outline"
					disabled={isSaving}
					onClick={onCancel}
				>
					Cancel
				</Button>
				<Button
					type="button"
					disabled={isSaving}
					onClick={() => {
						setSaveError(null);
						void onSave(draft.trim() ? draft : null).catch(() => {
							setSaveError(
								"Unable to save setup instructions. Your draft is retained; try again.",
							);
						});
					}}
				>
					{isSaving ? "Saving..." : "Save Instructions"}
				</Button>
			</DialogFooter>
			{saveError ? (
				<p role="alert" className="text-sm text-destructive">
					{saveError}
				</p>
			) : null}
		</>
	);
}

export function IntegrationSetupNotesDialog({
	open,
	onOpenChange,
	notes,
	onSave,
	isSaving = false,
}: IntegrationSetupNotesDialogProps) {
	return (
		<Dialog
			open={open}
			onOpenChange={(next) => !isSaving && onOpenChange(next)}
		>
			<DialogContent className="flex max-h-[92dvh] max-w-3xl flex-col overflow-hidden">
				{open && (
					<IntegrationSetupNotesEditor
						notes={notes}
						onSave={onSave}
						isSaving={isSaving}
						onCancel={() => onOpenChange(false)}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
