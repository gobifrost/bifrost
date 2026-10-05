import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
	icon: LucideIcon;
	title: string;
	description?: string;
	action?: ReactNode;
	className?: string;
}

/** What to show where a list or panel has nothing yet. */
export function EmptyState({
	icon: Icon,
	title,
	description,
	action,
	className,
}: EmptyStateProps) {
	return (
		<div
			data-slot="empty-state"
			className={cn(
				"flex flex-col items-center justify-center gap-1 rounded-[var(--bf-radius-surface)] border border-dashed border-border/70 px-4 py-8 text-center",
				className,
			)}
		>
			<Icon
				aria-hidden="true"
				className="mb-2 h-8 w-8 text-muted-foreground"
			/>
			<p className="text-sm font-medium">{title}</p>
			{description && (
				<p className="max-w-prose text-sm text-muted-foreground">
					{description}
				</p>
			)}
			{action && <div className="mt-3">{action}</div>}
		</div>
	);
}
