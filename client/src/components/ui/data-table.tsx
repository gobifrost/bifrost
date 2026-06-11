import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * DataTable - A standardized table component with sticky header/footer and internal scrolling.
 *
 * The table body scrolls while header and footer stay pinned. The component
 * sizes to its content by default — use `max-h-full` or a height-constrained
 * parent to cap it at available space.
 *
 * IMPORTANT: Parent containers must NOT have `overflow-auto` — DataTable owns
 * its own scroll context. Parents should use `flex-1 min-h-0` to provide
 * height constraints without creating a competing scroll container.
 *
 * Usage:
 * ```tsx
 * <DataTable>
 *   <DataTableHeader>
 *     <DataTableRow>
 *       <DataTableHead>Column</DataTableHead>
 *     </DataTableRow>
 *   </DataTableHeader>
 *   <DataTableBody>
 *     <DataTableRow onClick={() => navigate('/path')} clickable>
 *       <DataTableCell>Value</DataTableCell>
 *     </DataTableRow>
 *   </DataTableBody>
 *   <DataTableFooter>
 *     <DataTableRow>
 *       <DataTableCell colSpan={2}>Footer content</DataTableCell>
 *     </DataTableRow>
 *   </DataTableFooter>
 * </DataTable>
 * ```
 */

const DataTable = React.forwardRef<
	HTMLDivElement,
	React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => {
	// Extract footer outside the scroll container so the scrollbar
	// only covers the header + body area. Footer stays pinned at bottom.
	const footer: React.ReactNode[] = [];
	const rest: React.ReactNode[] = [];

	React.Children.forEach(children, (child) => {
		if (React.isValidElement(child) && child.type === DataTableFooter) {
			footer.push(child);
		} else {
			rest.push(child);
		}
	});

	return (
		<div
			ref={ref}
			className={cn(
				"overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10",
				"flex flex-col min-h-0 max-h-full",
				className,
			)}
			{...props}
		>
			<div className="overflow-auto flex-1 min-h-0">
				<table className="w-full text-sm">
					{rest}
				</table>
			</div>
			{footer.length > 0 && (
				<div className="flex-shrink-0 border-t">
					<table className="w-full text-sm">
						{footer}
					</table>
				</div>
			)}
		</div>
	);
});
DataTable.displayName = "DataTable";

const DataTableHeader = React.forwardRef<
	HTMLTableSectionElement,
	React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
	<thead
		ref={ref}
		className={cn(
			// Chrome band: base `background` (near-black in dark, white in light)
			// against the lighter `card` body — see "the elevation ladder" in
			// docs/research/ui-history-diagnosis.md.
			"sticky top-0 z-10 bg-background [&_tr]:border-b",
			className,
		)}
		{...props}
	/>
));
DataTableHeader.displayName = "DataTableHeader";

const DataTableBody = React.forwardRef<
	HTMLTableSectionElement,
	React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
	<tbody
		ref={ref}
		className={cn("[&_tr:last-child]:border-0", className)}
		{...props}
	/>
));
DataTableBody.displayName = "DataTableBody";

const DataTableFooter = React.forwardRef<
	HTMLTableSectionElement,
	React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
	<tfoot
		ref={ref}
		className={cn(
			// Chrome band — same `background` step as DataTableHeader.
			"bg-background font-medium [&>tr]:last:border-b-0",
			className,
		)}
		{...props}
	/>
));
DataTableFooter.displayName = "DataTableFooter";

interface DataTableRowProps extends React.HTMLAttributes<HTMLTableRowElement> {
	/** Makes the row appear clickable with cursor and hover state */
	clickable?: boolean;
	/** URL for Cmd/Ctrl+click and middle-click to open in new tab */
	href?: string;
}

const DataTableRow = React.forwardRef<HTMLTableRowElement, DataTableRowProps>(
	({ className, clickable, href, onClick, ...props }, ref) => {
		const handleClick = (e: React.MouseEvent<HTMLTableRowElement>) => {
			if (e.button === 1) return;
			if (href && (e.metaKey || e.ctrlKey)) {
				e.preventDefault();
				window.open(href, "_blank");
				return;
			}
			onClick?.(e);
		};

		const handleMouseUp = (e: React.MouseEvent<HTMLTableRowElement>) => {
			if (href && e.button === 1) {
				e.preventDefault();
				window.open(href, "_blank");
				return;
			}
			props.onMouseUp?.(e);
		};

		return (
			<tr
				ref={ref}
				className={cn(
					"border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
					(clickable || href) && "cursor-pointer",
					className,
				)}
				onClick={handleClick}
				onMouseUp={handleMouseUp}
				{...props}
			/>
		);
	},
);
DataTableRow.displayName = "DataTableRow";

const DataTableHead = React.forwardRef<
	HTMLTableCellElement,
	React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
	<th
		ref={ref}
		className={cn(
			"h-10 px-4 text-left align-middle font-medium whitespace-nowrap text-foreground [&:has([role=checkbox])]:pr-0",
			className,
		)}
		{...props}
	/>
));
DataTableHead.displayName = "DataTableHead";

const DataTableCell = React.forwardRef<
	HTMLTableCellElement,
	React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
	<td
		ref={ref}
		className={cn(
			"px-4 py-3 align-middle [&:has([role=checkbox])]:pr-0",
			className,
		)}
		{...props}
	/>
));
DataTableCell.displayName = "DataTableCell";

const DataTableCaption = React.forwardRef<
	HTMLTableCaptionElement,
	React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
	<caption
		ref={ref}
		className={cn("mt-4 text-sm text-muted-foreground", className)}
		{...props}
	/>
));
DataTableCaption.displayName = "DataTableCaption";

export {
	DataTable,
	DataTableHeader,
	DataTableBody,
	DataTableFooter,
	DataTableHead,
	DataTableRow,
	DataTableCell,
	DataTableCaption,
};
