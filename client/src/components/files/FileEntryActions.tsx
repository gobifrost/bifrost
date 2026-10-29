import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { StructureEntry } from "@/services/fileStructure";
import { ENTRY_ACTION_META, type EntryAction } from "./fileContextMenu";

export function FileEntryActions({
	entry,
	readOnly,
	policyReadOnly = false,
	downloading,
	onAction,
}: {
	entry: StructureEntry;
	readOnly: boolean;
	policyReadOnly?: boolean;
	downloading: boolean;
	onAction: (action: EntryAction, path: string) => void;
}) {
	const isFolder = entry.kind === "folder";
	const actions: EntryAction[] = isFolder
		? [
				"effective",
				"test",
				...(!readOnly ? (["upload"] as const) : []),
				...(!policyReadOnly ? (["newPolicy"] as const) : []),
			]
		: [
				"preview",
				"test",
				...(!policyReadOnly ? (["policy"] as const) : []),
				"download",
				...(!readOnly ? (["delete"] as const) : []),
			];
	return (
		<RecordActionsMenu label={`Actions for ${entry.name}`}>
			{actions.map((action) => {
				const Icon = ENTRY_ACTION_META[action].icon;
				return (
					<DropdownMenuItem
						key={action}
						className="min-h-11"
						variant={
							action === "delete" ? "destructive" : undefined
						}
						disabled={action === "download" && downloading}
						onSelect={() => onAction(action, entry.path)}
					>
						<Icon aria-hidden="true" className="size-4" />
						{ENTRY_ACTION_META[action].label}
					</DropdownMenuItem>
				);
			})}
		</RecordActionsMenu>
	);
}
