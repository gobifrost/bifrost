import type { ReactNode } from "react";

import { WorkspaceHeader } from "@/components/layout/WorkspaceHeader";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

export type WorkbenchCollection = {
	value: string;
	label: string;
};

type AgentWorkbenchFrameProps = {
	title: string;
	description?: string;
	collections: WorkbenchCollection[];
	collection: string;
	onCollectionChange: (collection: string) => void;
	toolbar: ReactNode;
	children: ReactNode;
	inspector?: ReactNode;
	onCloseInspector?: () => void;
	headerAction?: ReactNode;
	className?: string;
};

/**
 * Contained collection-and-inspector workspace shared by fleet and agent Workbench scopes.
 */
export function AgentWorkbenchFrame({
	title,
	description,
	collections,
	collection,
	onCollectionChange,
	toolbar,
	children,
	inspector,
	onCloseInspector,
	headerAction,
	className,
}: AgentWorkbenchFrameProps) {
	const selectedCollection = collections.find((item) => item.value === collection);

	return (
		<section
			className={cn(
				"flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card",
				className,
			)}
			aria-label={title}
		>
			<WorkspaceHeader className="h-auto min-h-14 flex-wrap gap-3 px-4 py-2">
				<div className="min-w-0 flex-1">
					<h2 className="font-display text-base font-semibold">{title}</h2>
					{description ? (
						<p className="text-xs text-muted-foreground">{description}</p>
					) : null}
				</div>
				<div className="flex items-center gap-2">
					<div className="md:hidden">
						<Select value={collection} onValueChange={onCollectionChange}>
							<SelectTrigger
								aria-label="Workbench collection"
								className="h-9 min-h-9 w-[11rem]"
							>
								<SelectValue>{selectedCollection?.label}</SelectValue>
							</SelectTrigger>
							<SelectContent>
								{collections.map((item) => (
									<SelectItem key={item.value} value={item.value}>
										{item.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
					{headerAction}
				</div>
			</WorkspaceHeader>

			<div className="flex min-h-0 min-w-0 flex-1">
				<nav
					aria-label="Workbench collections"
					className="hidden w-44 shrink-0 flex-col gap-1 border-r bg-muted/20 p-2 md:flex"
				>
					{collections.map((item) => (
						<Button
							key={item.value}
							type="button"
							variant="ghost"
							className={cn(
								"justify-start",
								item.value === collection &&
									"bg-muted font-medium text-foreground",
							)}
							aria-current={item.value === collection ? "page" : undefined}
							onClick={() => onCollectionChange(item.value)}
						>
							{item.label}
						</Button>
					))}
				</nav>

				<section
					className={cn(
						"flex min-h-0 min-w-0 flex-1 flex-col",
						inspector && "max-md:hidden",
					)}
				>
					{toolbar}
					<div className="min-h-0 flex-1 overflow-auto">{children}</div>
				</section>

				{inspector ? (
					<aside className="flex min-h-0 w-[min(28rem,42%)] shrink-0 flex-col border-l bg-card max-md:w-full max-md:border-l-0">
						<div className="flex min-h-12 shrink-0 items-center border-b px-3">
							{onCloseInspector ? (
								<Button
									type="button"
									variant="ghost"
									size="sm"
									onClick={onCloseInspector}
								>
									Close inspector
								</Button>
							) : null}
						</div>
						<div className="min-h-0 flex-1 overflow-auto p-4">{inspector}</div>
					</aside>
				) : null}
			</div>
		</section>
	);
}
