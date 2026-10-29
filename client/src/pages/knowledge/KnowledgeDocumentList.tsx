import {
	BookOpen,
	Building2,
	Globe,
	FileText,
	Trash2,
	Check,
	Calendar,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { cn } from "@/lib/utils";
export interface DocumentSummary {
	id: string;
	namespace: string;
	key: string | null;
	content_preview: string;
	metadata: Record<string, unknown>;
	organization_id: string | null;
	created_at: string | null;
}
function formatKnowledgeDate(value: string | null) {
	return value ? new Date(value).toLocaleDateString() : "-";
}
export function knowledgeDocumentTitle(doc: DocumentSummary) {
	return (
		doc.key ||
		doc.content_preview
			.split("\n")[0]
			.replace(/^#+\s*/, "")
			.trim()
			.slice(0, 100) ||
		doc.id
	);
}
function KnowledgeDocumentScopeBadge({
	organizationId,
	getOrgName,
}: {
	organizationId: string | null;
	getOrgName: (orgId: string | null | undefined) => string;
}) {
	const Icon = organizationId ? Building2 : Globe;
	return (
		<span className="inline-flex min-w-0 max-w-full items-center gap-1 text-xs leading-5 text-muted-foreground">
			<Icon aria-hidden="true" className="size-3.5 shrink-0" />
			<span className="min-w-0 [overflow-wrap:anywhere]">
				{organizationId ? getOrgName(organizationId) : "Global"}
			</span>
		</span>
	);
}

function KnowledgeDocumentRow({
	doc,
	selected,
	selectionMode,
	isChecked,
	getOrgName,
	canManage,
	disabled,
	onOpen,
	onToggleSelect,
	onDelete,
}: {
	doc: DocumentSummary;
	selected: boolean;
	selectionMode: boolean;
	isChecked: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	canManage: boolean;
	disabled: boolean;
	onOpen: (doc: DocumentSummary) => void;
	onToggleSelect: (id: string) => void;
	onDelete: (doc: DocumentSummary) => void;
}) {
	const title = knowledgeDocumentTitle(doc);
	const identity = doc.key || doc.id;
	const titleId = `knowledge-doc-${doc.id}-title`;
	return (
		<li
			aria-label={identity}
			aria-current={selected ? "true" : undefined}
			className={cn(
				"group flex min-h-16 items-stretch border-b border-border/70 transition-colors last:border-b-0 motion-reduce:transition-none",
				(selectionMode ? isChecked : selected)
					? "tree-row-selected"
					: "hover:bg-muted/20",
			)}
		>
			<article
				aria-labelledby={titleId}
				className="flex min-h-16 min-w-0 flex-1"
			>
				<button
					aria-labelledby={titleId}
					type="button"
					className="flex min-h-16 min-w-0 flex-1 items-start gap-3 px-3 py-3 text-left focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60"
					disabled={disabled}
					aria-pressed={selectionMode ? isChecked : undefined}
					onClick={() =>
						selectionMode ? onToggleSelect(doc.id) : onOpen(doc)
					}
				>
					<span className="flex size-9 shrink-0 items-center justify-center rounded-[var(--bf-radius-surface)] border border-primary/20 bg-primary/10 text-primary">
						{selectionMode && isChecked ? (
							<Check aria-hidden="true" className="size-4" />
						) : (
							<BookOpen aria-hidden="true" className="size-4" />
						)}
					</span>
					<span className="@container min-w-0 flex-1 space-y-1">
						<span
							id={titleId}
							className="block text-sm font-semibold leading-5 text-foreground [overflow-wrap:anywhere]"
						>
							{title}
						</span>
						{doc.key ? null : (
							<span className="block text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">
								{doc.id}
							</span>
						)}
						<span className="line-clamp-2 min-h-10 text-sm leading-5 text-muted-foreground [overflow-wrap:anywhere]">
							<MarkdownContent
								variant="preview"
								content={
									doc.content_preview ||
									"No preview available"
								}
							/>
						</span>
						<span className="grid min-w-0 grid-cols-[minmax(0,8rem)_minmax(0,12rem)] items-start gap-x-3 gap-y-1 pt-1 @[480px]:grid-cols-[8rem_12rem_7rem]">
							<KnowledgeDocumentScopeBadge
								organizationId={doc.organization_id}
								getOrgName={getOrgName}
							/>
							<span className="inline-flex min-w-0 items-start gap-1 text-xs leading-5 text-muted-foreground">
								<FileText
									aria-hidden="true"
									className="mt-0.5 size-3.5 shrink-0"
								/>
								<span className="min-w-0 break-words">
									{doc.namespace}
								</span>
							</span>
							<span className="inline-flex items-center gap-1 text-xs leading-5 text-muted-foreground">
								<Calendar
									aria-hidden="true"
									className="size-3.5 shrink-0"
								/>
								{formatKnowledgeDate(doc.created_at)}
							</span>
						</span>
					</span>
				</button>
			</article>
			{canManage && (
				<RecordActionsMenu label={`More actions for ${identity}`}>
					<DropdownMenuItem
						variant="destructive"
						className="min-h-11 whitespace-nowrap px-3"
						disabled={disabled}
						onClick={(event) => {
							event.stopPropagation();
							onDelete(doc);
						}}
					>
						<Trash2 className="mr-2 size-4" />
						Delete
					</DropdownMenuItem>
				</RecordActionsMenu>
			)}
		</li>
	);
}

export function KnowledgeDocumentList({
	documents,
	selectedDocId,
	selectionMode,
	canManageDocument,
	selectedIds,
	allVisibleSelected,
	getOrgName,
	busy,
	onToggleSelect,
	onToggleSelectAll,
	onOpen,
	onDelete,
}: {
	documents: DocumentSummary[];
	selectedDocId: string | null;
	selectionMode: boolean;
	canManageDocument: (doc: DocumentSummary) => boolean;
	selectedIds: Set<string>;
	allVisibleSelected: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	busy: boolean;
	onToggleSelect: (id: string) => void;
	onToggleSelectAll: () => void;
	onOpen: (doc: DocumentSummary) => void;
	onDelete: (doc: DocumentSummary) => void;
}) {
	return (
		<section
			className="flex min-h-0 flex-1 flex-col"
			aria-label="Knowledge documents"
		>
			{selectionMode && (
				<div className="flex min-h-11 shrink-0 items-center justify-between gap-3 border-b border-border/70 bg-muted/20 px-3 py-2">
					<span className="text-sm text-muted-foreground">
						{selectedIds.size} selected
					</span>
					<Button
						type="button"
						variant="ghost"
						className="h-9"
						disabled={busy || documents.length === 0}
						onClick={onToggleSelectAll}
					>
						{allVisibleSelected ? "Clear all" : "Select all"}
					</Button>
				</div>
			)}
			<ul
				aria-label="Knowledge documents"
				className="min-h-0 overflow-y-auto"
			>
				{documents.map((doc) => (
					<KnowledgeDocumentRow
						key={doc.id}
						doc={doc}
						selected={selectedDocId === doc.id}
						selectionMode={selectionMode}
						isChecked={selectedIds.has(doc.id)}
						getOrgName={getOrgName}
						canManage={canManageDocument(doc)}
						disabled={
							busy || (selectionMode && !canManageDocument(doc))
						}
						onToggleSelect={onToggleSelect}
						onOpen={onOpen}
						onDelete={onDelete}
					/>
				))}
			</ul>
		</section>
	);
}
