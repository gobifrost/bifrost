import { Fragment, useId, type ReactNode } from "react";
import { X } from "lucide-react";

import { WorkspaceHeader } from "@/components/layout/WorkspaceHeader";
import { Button } from "@/components/ui/button";
import { navigationSelectionClasses } from "@/components/layout/navigationStyles";
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
	group?: string;
	icon?: ReactNode;
	count?: number;
};

function detailLabel(collectionLabel: string) {
	if (collectionLabel === "Run History") return "Run details";
	if (collectionLabel.endsWith("ies"))
		return `${collectionLabel.slice(0, -3)}y details`;
	if (collectionLabel.endsWith("s"))
		return `${collectionLabel.slice(0, -1)} details`;
	return `${collectionLabel} details`;
}

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
	const selectedCollection = collections.find(
		(item) => item.value === collection,
	);
	const navigationId = useId();
	const collectionGroups = collections.reduce<
		Array<{ label?: string; items: WorkbenchCollection[] }>
	>((groups, item) => {
		const existing = groups.find((group) => group.label === item.group);
		if (existing) existing.items.push(item);
		else groups.push({ label: item.group, items: [item] });
		return groups;
	}, []);

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
					<h2 className="flex items-center gap-2 font-display text-base font-semibold">
						{selectedCollection?.icon ? (
							<span aria-hidden="true" className="text-primary">
								{selectedCollection.icon}
							</span>
						) : null}
						{title}
					</h2>
					{description ? (
						<p className="text-xs text-muted-foreground">
							{description}
						</p>
					) : null}
				</div>
				<div className="flex items-center gap-2">
					<div className="md:hidden">
						<Select
							value={collection}
							onValueChange={onCollectionChange}
						>
							<SelectTrigger
								aria-label="Workbench collection"
								className="h-9 min-h-9 w-[11rem]"
							>
								<SelectValue>
									{selectedCollection?.label}
								</SelectValue>
							</SelectTrigger>
							<SelectContent>
								{collections.map((item) => (
									<SelectItem
										key={item.value}
										value={item.value}
									>
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
					className="hidden w-40 shrink-0 flex-col border-r bg-muted/20 px-2 py-3 md:flex"
				>
					{collectionGroups.map((group, index) => (
						<div
							key={group.label ?? `collections-${index}`}
							className={cn(index > 0 && "mt-4 border-t pt-3")}
						>
							{group.label ? (
								<p className="mb-1 px-3 text-[0.6875rem] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
									{group.label}
								</p>
							) : null}
							{group.items.map((item) => (
								<Fragment key={item.value}>
									<button
										type="button"
										className={cn(
											"flex min-h-10 w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
											navigationSelectionClasses(
												item.value === collection,
											),
										)}
										aria-describedby={
											item.count === undefined
												? undefined
												: `${navigationId}-${item.value}-count`
										}
										aria-current={
											item.value === collection
												? "page"
												: undefined
										}
										onClick={() =>
											onCollectionChange(item.value)
										}
									>
										{item.icon ? (
											<span
												aria-hidden="true"
												className="shrink-0"
											>
												{item.icon}
											</span>
										) : null}
										<span className="min-w-0 flex-1 truncate">
											{item.label}
										</span>
										{item.count !== undefined ? (
											<span
												aria-hidden="true"
												className="font-mono text-xs tabular-nums text-muted-foreground"
											>
												{item.count}
											</span>
										) : null}
									</button>
									{item.count !== undefined ? (
										<span
											id={`${navigationId}-${item.value}-count`}
											className="sr-only"
										>
											{item.count} items
										</span>
									) : null}
								</Fragment>
							))}
						</div>
					))}
				</nav>

				<section
					role="region"
					aria-label={`${selectedCollection?.label ?? title} collection`}
					className={cn(
						"flex min-h-0 min-w-0 flex-1 flex-col",
						inspector && "max-lg:hidden",
					)}
				>
					{toolbar}
					<div className="min-h-0 flex-1 overflow-auto">
						{children}
					</div>
				</section>

				{inspector ? (
					<aside
						aria-label={detailLabel(
							selectedCollection?.label ?? title,
						)}
						className="relative flex min-h-0 w-[36%] min-w-[22rem] max-w-[30rem] shrink-0 flex-col border-l bg-card max-lg:min-w-0 max-lg:flex-1 max-lg:max-w-none max-md:w-full max-md:border-l-0"
					>
						{onCloseInspector ? (
							<Button
								type="button"
								variant="ghost"
								size="icon-sm"
								aria-label="Close Inspector"
								className="absolute right-2 top-2 z-10"
								onClick={onCloseInspector}
							>
								<X aria-hidden="true" className="size-4" />
							</Button>
						) : null}
						<div className="min-h-0 flex-1 overflow-auto p-4 pr-12">
							{inspector}
						</div>
					</aside>
				) : null}
			</div>
		</section>
	);
}
