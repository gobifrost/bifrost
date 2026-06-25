/**
 * SolutionReadmeTab
 *
 * The README surface for a Solution install — setup instructions authored in
 * markdown via the shared TipTap editor. Presentational and side-effect-free:
 * the parent owns the PUT mutation through `onSave`. Local state tracks the
 * view/edit toggle and the in-flight draft so Cancel reverts cleanly.
 *
 *   - readme has content → render it read-only; `canEdit` exposes an Edit toggle.
 *   - readme empty + canEdit → an "Add setup instructions" empty state.
 *   - readme empty + !canEdit → a muted "No setup instructions provided." state.
 */

import { useState } from "react";
import { FileText, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TiptapEditor } from "@/components/ui/tiptap-editor";

export interface SolutionReadmeTabProps {
	readme: string | null;
	canEdit: boolean;
	onSave?: (markdown: string) => void | Promise<void>;
}

export function SolutionReadmeTab({
	readme,
	onSave,
	canEdit,
}: SolutionReadmeTabProps) {
	const [editing, setEditing] = useState(false);
	const [draft, setDraft] = useState("");
	const [saving, setSaving] = useState(false);

	const hasContent = Boolean(readme && readme.trim());

	const startEditing = () => {
		setDraft(readme ?? "");
		setEditing(true);
	};

	const cancelEditing = () => {
		setEditing(false);
		setDraft("");
	};

	const save = async () => {
		if (!onSave) return;
		setSaving(true);
		try {
			await onSave(draft);
			setEditing(false);
			setDraft("");
		} finally {
			setSaving(false);
		}
	};

	if (editing) {
		return (
			<div className="flex h-full flex-col gap-3">
				<div className="min-h-0 flex-1">
					<TiptapEditor
						content={draft}
						onChange={setDraft}
						placeholder="Write setup instructions in markdown…"
						className="h-full"
					/>
				</div>
				<div className="flex shrink-0 items-center justify-end gap-2">
					<Button variant="outline" onClick={cancelEditing} disabled={saving}>
						Cancel
					</Button>
					<Button onClick={() => void save()} disabled={saving}>
						Save
					</Button>
				</div>
			</div>
		);
	}

	if (!hasContent) {
		if (!canEdit) {
			return (
				<div className="rounded-2xl border border-dashed py-12 text-center text-sm text-muted-foreground">
					No setup instructions provided.
				</div>
			);
		}
		return (
			<div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed py-12 text-center">
				<FileText className="h-8 w-8 text-muted-foreground/60" />
				<div className="space-y-1">
					<p className="text-sm font-medium">Add setup instructions</p>
					<p className="text-sm text-muted-foreground">
						Document how to configure and use this Solution.
					</p>
				</div>
				<Button onClick={startEditing}>
					<Pencil className="mr-1.5 h-4 w-4" />
					Write README
				</Button>
			</div>
		);
	}

	return (
		<div className="flex h-full flex-col gap-3">
			{canEdit && (
				<div className="flex shrink-0 items-center justify-end">
					<Button variant="outline" size="sm" onClick={startEditing}>
						<Pencil className="mr-1.5 h-4 w-4" />
						Edit
					</Button>
				</div>
			)}
			<div className="min-h-0 flex-1">
				<TiptapEditor content={readme ?? ""} readOnly className="h-full" />
			</div>
		</div>
	);
}
