import type { KeyboardEvent, ReactNode } from "react";

import { cn } from "@/lib/utils";

type WorkbenchRowProps = {
	title: ReactNode;
	meta?: ReactNode;
	selected?: boolean;
	onSelect: () => void;
	tabIndex?: 0 | -1;
	selectionControl?: ReactNode;
	actions?: ReactNode;
	children?: ReactNode;
	className?: string;
};

/** A keyboard-accessible row whose selection is visible beyond color alone. */
export function WorkbenchRow({
	title,
	meta,
	selected = false,
	onSelect,
	tabIndex = 0,
	selectionControl,
	actions,
	children,
	className,
}: WorkbenchRowProps) {
	function setGridTabStop(row: HTMLDivElement) {
		const rows = Array.from(
			row.parentElement?.querySelectorAll<HTMLDivElement>('[role="row"]') ?? [],
		);
		rows.forEach((item) => {
			item.tabIndex = item === row ? 0 : -1;
		});
	}

	function moveFocus(row: HTMLDivElement, offset: number | "start" | "end") {
		const rows = Array.from(
			row.parentElement?.querySelectorAll<HTMLDivElement>('[role="row"]') ?? [],
		);
		const currentIndex = rows.indexOf(row);
		const targetIndex =
			offset === "start"
				? 0
				: offset === "end"
					? rows.length - 1
					: Math.min(Math.max(currentIndex + offset, 0), rows.length - 1);
		const target = rows[targetIndex];
		if (!target) return;
		setGridTabStop(target);
		target.focus();
	}

	function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
		if (event.key === "ArrowDown") {
			event.preventDefault();
			moveFocus(event.currentTarget, 1);
			return;
		}
		if (event.key === "ArrowUp") {
			event.preventDefault();
			moveFocus(event.currentTarget, -1);
			return;
		}
		if (event.key === "Home") {
			event.preventDefault();
			moveFocus(event.currentTarget, "start");
			return;
		}
		if (event.key === "End") {
			event.preventDefault();
			moveFocus(event.currentTarget, "end");
			return;
		}
		if (event.key === "Enter" || event.key === " ") {
			event.preventDefault();
			setGridTabStop(event.currentTarget);
			onSelect();
		}
	}

	return (
		<div
			role="row"
			tabIndex={tabIndex}
			aria-selected={selected}
			onClick={(event) => {
				setGridTabStop(event.currentTarget);
				onSelect();
			}}
			onKeyDown={onKeyDown}
			className={cn(
				"flex w-full cursor-pointer items-start gap-3 border-b px-4 py-3 text-left outline-none transition-colors hover:bg-muted/60 focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset active:bg-muted",
				selected && "tree-row-selected",
				className,
			)}
		>
			{selectionControl ? (
				<div
					role="gridcell"
					className="shrink-0 pt-0.5"
					onClick={(event) => event.stopPropagation()}
					onKeyDown={(event) => event.stopPropagation()}
				>
					{selectionControl}
				</div>
			) : null}
			<div role="gridcell" className="min-w-0 flex-1">
				<div className="font-medium [overflow-wrap:anywhere]">{title}</div>
				{meta ? (
					<div className="mt-1 text-sm text-muted-foreground [overflow-wrap:anywhere]">
						{meta}
					</div>
				) : null}
				{children}
			</div>
			{actions ? (
				<div
					role="gridcell"
					className="shrink-0"
					onClick={(event) => event.stopPropagation()}
					onKeyDown={(event) => event.stopPropagation()}
				>
					{actions}
				</div>
			) : null}
		</div>
	);
}
