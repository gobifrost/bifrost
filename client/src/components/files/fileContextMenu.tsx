import {
	Download,
	Eye,
	FlaskConical,
	FolderPlus,
	ShieldCheck,
	ShieldPlus,
	Trash2,
	Upload,
} from "lucide-react";
import {
	ContextMenuItem,
	ContextMenuSeparator,
} from "@/components/ui/context-menu";

/**
 * Canonical labels + icons for file/share/folder actions, shared by the tree
 * (ShareTree) and the file rows (FolderListing) so both menus read the same.
 * Test access uses a distinct flask icon (NOT the shield, which means policy).
 */
export const ENTRY_ACTION_META = {
	preview: { label: "Preview", icon: Eye },
	effective: { label: "Effective access", icon: ShieldCheck },
	test: { label: "Test access", icon: FlaskConical },
	policy: { label: "Manage policy", icon: ShieldCheck },
	newPolicy: { label: "New policy here", icon: ShieldPlus },
	upload: { label: "Upload here", icon: Upload },
	newFolder: { label: "New folder", icon: FolderPlus },
	download: { label: "Download", icon: Download },
	delete: { label: "Delete", icon: Trash2 },
} as const;

export type EntryAction = keyof typeof ENTRY_ACTION_META;

export function EntryMenuItem({
	action,
	onSelect,
	destructive,
}: {
	action: EntryAction;
	onSelect: () => void;
	destructive?: boolean;
}) {
	const meta = ENTRY_ACTION_META[action];
	const Icon = meta.icon;
	return (
		<ContextMenuItem
			variant={destructive ? "destructive" : undefined}
			onSelect={onSelect}
		>
			<Icon className="h-4 w-4" /> {meta.label}
		</ContextMenuItem>
	);
}

export { ContextMenuSeparator };
