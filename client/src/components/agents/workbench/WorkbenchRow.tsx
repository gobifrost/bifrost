import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type WorkbenchRowProps = {
	title: ReactNode;
	meta?: ReactNode;
	selected?: boolean;
	onSelect: () => void;
	selectionControl?: ReactNode;
	actions?: ReactNode;
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
	className,
}: WorkbenchRowProps) {
	return (
		<div
			role="listitem"
			className={cn(
				"flex w-full items-start gap-3 border-b px-4 py-3",
				selected && "tree-row-selected",
				className,
			)}
		>
			{selectionControl ? (
				<div className="shrink-0 pt-0.5">
					{selectionControl}
				</div>
			) : null}
			<button
				type="button"
				aria-current={selected ? "true" : undefined}
				onClick={onSelect}
				className="flex min-w-0 flex-1 flex-col items-start rounded-[var(--bf-radius-control)] text-left outline-none transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset active:bg-muted"
			>
				<span className="font-medium [overflow-wrap:anywhere]">{title}</span>
				{meta ? (
					<span className="mt-1 text-sm text-muted-foreground [overflow-wrap:anywhere]">
						{meta}
					</span>
				) : null}
			</button>
			{actions ? (
				<div className="shrink-0">
					{actions}
				</div>
			) : null}
		</div>
	);
}
