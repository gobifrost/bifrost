import { Building2, Power, PowerOff, Shield, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

export interface BulkActionBarProps {
	/** Count of selected items. The bar is hidden when this is 0. */
	count: number;
	/** Mix of active/inactive in the selection — controls which power buttons appear. */
	activeMix: "all_active" | "all_inactive" | "mixed";
	/** Which operations the caller may offer (the server still decides
	 * each user, and reports per-user failures). */
	canMoveOrg: boolean;
	canReplaceRoles: boolean;
	canSetActive: boolean;
	/** Clear-selection callback. */
	onClear: () => void;
	onMoveOrg: () => void;
	onReplaceRoles: () => void;
	onDisable: () => void;
	onEnable: () => void;
	className?: string;
}

/**
 * Sticky bottom action bar that appears when one or more users are selected.
 *
 * Active-mix logic:
 *  - all_active: show only "Disable"
 *  - all_inactive: show only "Enable"
 *  - mixed: show both
 */
export function BulkActionBar({
	count,
	activeMix,
	canMoveOrg,
	canReplaceRoles,
	canSetActive,
	onClear,
	onMoveOrg,
	onReplaceRoles,
	onDisable,
	onEnable,
	className,
}: BulkActionBarProps) {
	if (count === 0) return null;

	const showDisable = canSetActive && activeMix !== "all_inactive";
	const showEnable = canSetActive && activeMix !== "all_active";

	return (
		<div
			role="region"
			aria-label="Bulk user actions"
			className={cn(
				"sticky bottom-4 left-0 right-0 mx-auto grid grid-cols-2 items-center gap-2 rounded-[var(--bf-radius-feature)] border sm:flex sm:flex-wrap sm:gap-3 bg-popover px-4 py-2 shadow-lg ring-1 ring-foreground/5 dark:ring-foreground/10",
				"w-full max-w-3xl z-20",
				className,
			)}
		>
			<span className="text-sm font-medium">{count} selected</span>
			<div className="justify-self-end sm:order-last sm:ml-auto">
				<Button
					variant="ghost"
					size="sm"
					onClick={onClear}
					aria-label="Clear selection"
					className="h-11 w-11 sm:h-8 sm:w-8"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>
			<Separator orientation="vertical" className="hidden h-6 sm:block" />

			{canMoveOrg && (
				<Button
					variant="ghost"
					size="sm"
					className="min-h-11 sm:min-h-0"
					onClick={onMoveOrg}
				>
					<Building2 className="h-4 w-4 mr-1.5" />
					Move to org
				</Button>
			)}
			{canReplaceRoles && (
				<Button
					variant="ghost"
					size="sm"
					className="min-h-11 sm:min-h-0"
					onClick={onReplaceRoles}
				>
					<Shield className="h-4 w-4 mr-1.5" />
					Replace roles
				</Button>
			)}
			{showDisable && (
				<Button
					variant="ghost"
					size="sm"
					className="min-h-11 sm:min-h-0"
					onClick={onDisable}
				>
					<PowerOff className="h-4 w-4 mr-1.5" />
					Disable
				</Button>
			)}
			{showEnable && (
				<Button
					variant="ghost"
					size="sm"
					className="min-h-11 sm:min-h-0"
					onClick={onEnable}
				>
					<Power className="h-4 w-4 mr-1.5" />
					Enable
				</Button>
			)}
		</div>
	);
}
