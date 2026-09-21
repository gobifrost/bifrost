import type { ComponentProps, ReactNode } from "react";
import { Search } from "lucide-react";

import { WorkspacePrimaryAction } from "@/components/layout/WorkspacePrimaryAction";
import { Input } from "@/components/ui/input";

type WorkbenchCollectionToolbarProps = {
	collectionLabel: string;
	search: string;
	onSearchChange: (search: string) => void;
	selectionCount?: number;
	secondaryAction?: ReactNode;
	primaryAction?: ComponentProps<typeof WorkspacePrimaryAction>;
	children?: ReactNode;
};

/** Shared collection controls; collection-specific context stays attached to its rows. */
export function WorkbenchCollectionToolbar({
	collectionLabel,
	search,
	onSearchChange,
	selectionCount = 0,
	secondaryAction,
	primaryAction,
	children,
}: WorkbenchCollectionToolbarProps) {
	return (
		<div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/10 p-3">
			<div className="relative min-w-[12rem] flex-1">
				<Search
					aria-hidden="true"
					className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
				/>
				<label className="sr-only" htmlFor="workbench-collection-search">
					Search {collectionLabel}
				</label>
				<Input
					id="workbench-collection-search"
					value={search}
					onChange={(event) => onSearchChange(event.target.value)}
					placeholder={`Search ${collectionLabel.toLowerCase()}`}
					className="pl-9"
				/>
			</div>
			{selectionCount > 0 ? (
				<span className="text-sm text-muted-foreground">
					{selectionCount} selected
				</span>
			) : null}
			{children}
			{secondaryAction}
			{primaryAction ? <WorkspacePrimaryAction {...primaryAction} /> : null}
		</div>
	);
}
