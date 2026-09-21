import type { KeyboardEvent, ReactNode } from "react";

import { cn } from "@/lib/utils";

type WorkbenchRowProps = {
	title: ReactNode;
	meta?: ReactNode;
	selected?: boolean;
	onSelect: () => void;
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
	selectionControl,
	actions,
	children,
	className,
}: WorkbenchRowProps) {
	function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
		if (event.key !== "Enter" && event.key !== " ") return;
		event.preventDefault();
		onSelect();
	}

	return (
		<div
			role="row"
			tabIndex={0}
			aria-selected={selected}
			onClick={onSelect}
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
