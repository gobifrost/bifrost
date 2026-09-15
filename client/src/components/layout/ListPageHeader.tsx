import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface ListPageHeaderProps {
	title: ReactNode;
	titleSlot?: ReactNode;
	titleAccessory?: ReactNode;
	description?: ReactNode;
	actions?: ReactNode;
	children?: ReactNode;
	className?: string;
	titleClassName?: string;
	descriptionClassName?: string;
	actionsClassName?: string;
}

export function ListPageHeader({
	title,
	titleSlot,
	titleAccessory,
	description,
	actions,
	children,
	className,
	titleClassName,
	descriptionClassName,
	actionsClassName,
}: ListPageHeaderProps) {
	return (
		<div
			className={cn(
				"flex min-w-0 shrink-0 flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between",
				className,
			)}
		>
			<div className="min-w-0 flex-1 sm:basis-64">
				<div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3">
					{titleSlot ?? (
						<h1
							className={cn(
								"[overflow-wrap:anywhere] font-display text-2xl font-semibold tracking-tight sm:text-3xl",
								titleClassName,
							)}
						>
							{title}
						</h1>
					)}
					{titleAccessory}
				</div>
				{description && (
					<p
						className={cn(
							"mt-2 max-w-2xl [overflow-wrap:anywhere] text-sm leading-6 text-muted-foreground",
							descriptionClassName,
						)}
					>
						{description}
					</p>
				)}
				{children}
			</div>
			{actions && (
				<div
					className={cn(
						"flex min-w-0 max-w-full flex-wrap items-center gap-2 sm:justify-end",
						actionsClassName,
					)}
				>
					{actions}
				</div>
			)}
		</div>
	);
}
