import type { ComponentType, ReactNode } from "react";
import { Building2, Power, PowerOff, Shield, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

/**
 * Sticky bottom action bar that appears when one or more rows are selected:
 * the count, the actions the page offers, and a clear button.
 */
export function BulkActionBar({
	count,
	label,
	onClear,
	children,
	className,
}: {
	/** Count of selected items. The bar is hidden when this is 0. */
	count: number;
	/** The region's accessible name, e.g. "Bulk user actions". */
	label: string;
	onClear: () => void;
	/** BulkActionButtons. */
	children: ReactNode;
	className?: string;
}) {
	if (count === 0) return null;

	return (
		<div
			role="region"
			aria-label={label}
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
			{children}
		</div>
	);
}

export function BulkActionButton({
	icon: Icon,
	onClick,
	disabled,
	title,
	children,
}: {
	icon: ComponentType<{ className?: string }>;
	onClick: () => void;
	disabled?: boolean;
	/** Why the action is disabled, or what it will skip. */
	title?: string;
	children: ReactNode;
}) {
	return (
		<Button
			variant="ghost"
			size="sm"
			className="min-h-11 sm:min-h-0"
			onClick={onClick}
			disabled={disabled}
			title={title}
		>
			<Icon className="h-4 w-4 mr-1.5" />
			{children}
		</Button>
	);
}

export interface UserBulkActionBarProps {
	/** Count of selected users. The bar is hidden when this is 0. */
	count: number;
	/** Mix of active/inactive in the selection — controls which power buttons appear. */
	activeMix: "all_active" | "all_inactive" | "mixed";
	/** Which operations the caller may offer (the server still decides
	 * each user, and reports per-user failures). */
	canMoveOrg: boolean;
	canReplaceRoles: boolean;
	canSetActive: boolean;
	onClear: () => void;
	onMoveOrg: () => void;
	onReplaceRoles: () => void;
	onDisable: () => void;
	onEnable: () => void;
}

/**
 * The Users list's bulk actions.
 *
 * Active-mix logic:
 *  - all_active: show only "Disable"
 *  - all_inactive: show only "Enable"
 *  - mixed: show both
 */
export function UserBulkActionBar({
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
}: UserBulkActionBarProps) {
	const showDisable = canSetActive && activeMix !== "all_inactive";
	const showEnable = canSetActive && activeMix !== "all_active";

	return (
		<BulkActionBar
			count={count}
			label="Bulk user actions"
			onClear={onClear}
		>
			{canMoveOrg && (
				<BulkActionButton icon={Building2} onClick={onMoveOrg}>
					Move to Org
				</BulkActionButton>
			)}
			{canReplaceRoles && (
				<BulkActionButton icon={Shield} onClick={onReplaceRoles}>
					Replace Roles
				</BulkActionButton>
			)}
			{showDisable && (
				<BulkActionButton icon={PowerOff} onClick={onDisable}>
					Disable
				</BulkActionButton>
			)}
			{showEnable && (
				<BulkActionButton icon={Power} onClick={onEnable}>
					Enable
				</BulkActionButton>
			)}
		</BulkActionBar>
	);
}
