import type { MouseEvent } from "react";
import { KeyRound, Pencil, Trash2 } from "lucide-react";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { EventSource } from "@/services/events";

export function EventSourceActions({
	source,
	onEdit,
	onDelete,
	onRotateSecret,
}: {
	source: EventSource;
	onEdit: (source: EventSource, event: MouseEvent) => void;
	onDelete: (source: EventSource, event: MouseEvent) => void;
	onRotateSecret?: (source: EventSource, event: MouseEvent) => void;
}) {
	return (
		<RecordActionsMenu label={`${source.name} actions`}>
			<DropdownMenuItem
				className="min-h-11"
				onClick={(event) => onEdit(source, event)}
			>
				<Pencil aria-hidden="true" className="size-4" />
				Edit
			</DropdownMenuItem>
			{onRotateSecret && (
				<DropdownMenuItem
					className="min-h-11"
					onClick={(event) => onRotateSecret(source, event)}
				>
					<KeyRound aria-hidden="true" className="size-4" />
					Rotate signing secret
				</DropdownMenuItem>
			)}
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11"
				onClick={(event) => onDelete(source, event)}
			>
				<Trash2 aria-hidden="true" className="size-4" />
				Delete
			</DropdownMenuItem>
		</RecordActionsMenu>
	);
}
