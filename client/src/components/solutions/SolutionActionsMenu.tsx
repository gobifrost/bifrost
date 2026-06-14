import {
	Download,
	HardDriveUpload,
	Loader2,
	MoreVertical,
	Pencil,
	Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props {
	exporting: boolean;
	onCapture: () => void;
	onExport: () => void;
	onEdit: () => void;
	onDelete: () => void;
}

/**
 * Overflow menu for the secondary Solution actions. The primary action
 * ("Update…") stays a visible button on the detail header; everything else —
 * Capture, Export, Edit, and the destructive Delete — collapses here, matching
 * the platform's admin-detail convention (see UserActionsMenu).
 */
export function SolutionActionsMenu({
	exporting,
	onCapture,
	onExport,
	onEdit,
	onDelete,
}: Props) {
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="icon"
					aria-label="More solution actions"
					data-testid="solution-actions"
				>
					<MoreVertical className="h-4 w-4" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="w-auto">
				<DropdownMenuItem
					onClick={onCapture}
					className="whitespace-nowrap"
					data-testid="capture-solution"
				>
					<HardDriveUpload className="mr-2 h-4 w-4" />
					Capture Existing Entities
				</DropdownMenuItem>
				<DropdownMenuItem
					onClick={onExport}
					disabled={exporting}
					className="whitespace-nowrap"
					data-testid="export-solution"
				>
					{exporting ? (
						<Loader2 className="mr-2 h-4 w-4 animate-spin" />
					) : (
						<Download className="mr-2 h-4 w-4" />
					)}
					Export Solution
				</DropdownMenuItem>
				<DropdownMenuItem
					onClick={onEdit}
					className="whitespace-nowrap"
					data-testid="edit-solution"
				>
					<Pencil className="mr-2 h-4 w-4" />
					Edit Details
				</DropdownMenuItem>
				<DropdownMenuSeparator />
				<DropdownMenuItem
					onClick={onDelete}
					className="whitespace-nowrap text-destructive focus:text-destructive"
					data-testid="delete-solution"
				>
					<Trash2 className="mr-2 h-4 w-4" />
					Delete Solution
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
