import type { ReactNode } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { DataTableCell, DataTableHead } from "@/components/ui/data-table";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import type { SelectableItem, UserSelection } from "@/hooks/useUserSelection";
import { cn } from "@/lib/utils";

type Selection = UserSelection<SelectableItem>;

/** Selects or clears every visible row: the table header and the phone list's bar. */
export function SelectAllCheckbox({
	selection,
	label,
}: {
	selection: Pick<
		Selection,
		"allVisibleSelected" | "someVisibleSelected" | "toggleAllVisible"
	>;
	label: string;
}) {
	return (
		<Checkbox
			aria-label={label}
			checked={
				selection.allVisibleSelected
					? true
					: selection.someVisibleSelected
						? "indeterminate"
						: false
			}
			onCheckedChange={() => selection.toggleAllVisible()}
		/>
	);
}

/**
 * One row's checkbox; shift-click selects a range. A row that can't be
 * selected shows a disabled checkbox named for why, with the reason on hover.
 */
export function RowSelectCheckbox({
	selection,
	id,
	label,
	unselectable,
}: {
	selection: Pick<Selection, "isSelected" | "toggle">;
	id: string;
	label: string;
	unselectable?: { label: string; reason: string };
}) {
	if (unselectable)
		return (
			<Tooltip>
				<TooltipTrigger asChild>
					<span>
						<Checkbox
							checked={false}
							disabled
							aria-label={unselectable.label}
						/>
					</span>
				</TooltipTrigger>
				<TooltipContent>{unselectable.reason}</TooltipContent>
			</Tooltip>
		);
	return (
		<Checkbox
			aria-label={label}
			checked={selection.isSelected(id)}
			onClick={(event) => {
				selection.toggle(id, { shiftKey: event.shiftKey });
				event.preventDefault();
			}}
		/>
	);
}

/** The phone record's checkbox, at a full touch-target size. */
export function RecordSelectTarget({ children }: { children: ReactNode }) {
	return (
		<label className="flex h-11 w-11 shrink-0 items-center justify-center">
			{children}
		</label>
	);
}

/** The bar above a phone record list: select every record, and any sort. */
export function RecordListBar({ children }: { children: ReactNode }) {
	return (
		<div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-2">
			{children}
		</div>
	);
}

/** "Select Page" (or another label) beside the select-all checkbox, for phones. */
export function RecordSelectAll({
	selection,
	label,
	children,
}: {
	selection: Pick<
		Selection,
		"allVisibleSelected" | "someVisibleSelected" | "toggleAllVisible"
	>;
	label: string;
	children: ReactNode;
}) {
	return (
		<label className="flex min-h-11 items-center gap-3 text-sm">
			<SelectAllCheckbox selection={selection} label={label} />
			{children}
		</label>
	);
}

/** The table's select-all column header. */
export function SelectionHead(props: Parameters<typeof SelectAllCheckbox>[0]) {
	return (
		<DataTableHead className="w-0 whitespace-nowrap">
			<SelectAllCheckbox {...props} />
		</DataTableHead>
	);
}

/** The row's checkbox cell; clicking it doesn't open the row. */
export function SelectionCell(props: Parameters<typeof RowSelectCheckbox>[0]) {
	return (
		<DataTableCell
			className="w-0 whitespace-nowrap"
			onClick={(event) => event.stopPropagation()}
		>
			<RowSelectCheckbox {...props} />
		</DataTableCell>
	);
}

export function ActionsHead() {
	return (
		<DataTableHead className="sticky right-0 w-px whitespace-nowrap bg-muted text-right">
			Actions
		</DataTableHead>
	);
}

/**
 * The row's ⋮ menu, pinned to the right edge while the table scrolls
 * sideways; clicking it doesn't open the row. The row needs `group/row`.
 * `className` replaces the opaque surface for a tinted row.
 */
export function ActionsCell({
	children,
	className,
}: {
	children: ReactNode;
	className?: string;
}) {
	return (
		<DataTableCell
			className={cn(
				"sticky right-0 w-px whitespace-nowrap bg-card text-right group-hover/row:bg-[color-mix(in_oklch,var(--card),var(--muted)_50%)]",
				className,
			)}
			onClick={(event) => event.stopPropagation()}
		>
			{children}
		</DataTableCell>
	);
}
