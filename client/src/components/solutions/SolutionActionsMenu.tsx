import {
	Download,
	HardDriveUpload,
	Loader2,
	MoreVertical,
	Pencil,
	PowerOff,
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
	/** Whether this install is currently inactive (status === "inactive"). */
	isInactive: boolean;
	onCapture: () => void;
	onExport: () => void;
	onEdit: () => void;
	/** Non-destructive uninstall → flips to inactive. Only shown when active. */
	onUninstall: () => void;
	/** Hard-delete: permanently destroys everything. Always available. */
	onHardDelete: () => void;
}

/**
 * Overflow menu for the secondary Solution actions. The primary action
 * ("Update…") stays a visible button on the detail header; everything else —
 * Capture, Export, Edit, Uninstall (non-destructive), and the permanent
 * Delete — collapses here, matching the platform's admin-detail convention.
 */
export function SolutionActionsMenu({
	exporting,
	isInactive,
	onCapture,
	onExport,
	onEdit,
	onUninstall,
	onHardDelete,
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
				{!isInactive && (
					<DropdownMenuItem
						onClick={onUninstall}
						className="whitespace-nowrap"
						data-testid="uninstall-solution"
					>
						<PowerOff className="mr-2 h-4 w-4" />
						Uninstall
					</DropdownMenuItem>
				)}
				<DropdownMenuItem
					onClick={onHardDelete}
					className="whitespace-nowrap text-destructive focus:text-destructive"
					data-testid="hard-delete-solution"
				>
					<Trash2 className="mr-2 h-4 w-4" />
					Delete permanently
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
