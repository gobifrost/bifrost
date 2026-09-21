import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type WorkbenchRowProps = {
	title: ReactNode;
	meta?: ReactNode;
	leading?: ReactNode;
	leadingLabel?: string;
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
	leading,
	leadingLabel,
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
				"group flex w-full items-start gap-3 border-b px-4 py-3 transition-colors hover:bg-muted/40",
				selected && "tree-row-selected",
				className,
			)}
		>
			{selectionControl ? (
				<div className="shrink-0 pt-0.5">{selectionControl}</div>
			) : null}
			{leading ? (
				<span
					role={leadingLabel ? "img" : undefined}
					aria-label={leadingLabel}
					aria-hidden={leadingLabel ? undefined : true}
					className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] border bg-muted/50 text-muted-foreground group-hover:border-border"
				>
					{leading}
				</span>
			) : null}
			<button
				type="button"
				aria-current={selected ? "true" : undefined}
				onClick={onSelect}
				className="flex min-w-0 flex-1 flex-col items-start rounded-[var(--bf-radius-control)] text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 focus-visible:ring-offset-background"
			>
				<span className="font-medium [overflow-wrap:anywhere]">
					{title}
				</span>
				{meta ? (
					<span className="mt-1 text-sm text-muted-foreground [overflow-wrap:anywhere]">
						{meta}
					</span>
				) : null}
			</button>
			{actions ? <div className="shrink-0">{actions}</div> : null}
		</div>
	);
}
