import { useMemo, useState } from "react";
import { Building2, Plus, Star, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { SearchBox } from "@/components/search/SearchBox";

export interface OrgInfo {
	id: string | null;
	name: string;
	isProvider?: boolean;
}

export interface ConsumerTabItem {
	id: string;
	primary: string;
	secondary?: string | null;
	/** Org info for the Organization column. Null = platform/global. */
	org?: OrgInfo | null;
}

export interface ConsumerTabProps {
	items: ConsumerTabItem[];
	isLoading: boolean;
	candidates: ConsumerTabItem[];
	candidatesLoading: boolean;
	consumerLabel: string;
	emptyHint: string;
	/** Header label for the primary column (defaults to "Name"). */
	primaryColumnLabel?: string;
	/** Header label for the secondary column (defaults to "Description"). */
	secondaryColumnLabel?: string;
	/** Hide the secondary column entirely (e.g. when item.secondary is always null). */
	hideSecondary?: boolean;
	/** Show an Organization column. Items must populate `org`. */
	showOrgColumn?: boolean;
	onAssign: (ids: string[]) => Promise<void>;
	onUnassign: (ids: string[]) => Promise<void>;
}

/**
 * Generic role-consumer tab. Renders the assigned items as a standard
 * DataTable (matching /users + /history conventions), with an optional
 * Organization column for entity types that carry an org_id.
 *
 * Knowledge is a special case — see KnowledgeTab in RoleDetail.tsx for the
 * namespace+org shape.
 */
export function ConsumerTab({
	items,
	isLoading,
	candidates,
	candidatesLoading,
	consumerLabel,
	emptyHint,
	primaryColumnLabel = "Name",
	secondaryColumnLabel = "Description",
	hideSecondary = false,
	showOrgColumn = false,
	onAssign,
	onUnassign,
}: ConsumerTabProps) {
	const [search, setSearch] = useState("");
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [drawerOpen, setDrawerOpen] = useState(false);
	const [submitting, setSubmitting] = useState(false);

	const visibleItems = useMemo(() => {
		const q = search.trim().toLowerCase();
		if (!q) return items;
		return items.filter(
			(it) =>
				it.primary.toLowerCase().includes(q) ||
				(it.secondary ?? "").toLowerCase().includes(q) ||
				(it.org?.name ?? "").toLowerCase().includes(q),
		);
	}, [items, search]);

	const visibleIdSet = useMemo(
		() => new Set(visibleItems.map((i) => i.id)),
		[visibleItems],
	);

	const effectiveSelected = useMemo(() => {
		const out = new Set<string>();
		for (const id of selected) if (visibleIdSet.has(id)) out.add(id);
		return out;
	}, [selected, visibleIdSet]);

	const allVisibleSelected =
		visibleItems.length > 0 &&
		visibleItems.every((i) => effectiveSelected.has(i.id));
	const someVisibleSelected =
		!allVisibleSelected && effectiveSelected.size > 0;

	const toggleOne = (id: string) =>
		setSelected((prev) => {
			const next = new Set<string>();
			for (const sid of prev) if (visibleIdSet.has(sid)) next.add(sid);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const toggleAll = () =>
		setSelected((prev) => {
			const next = new Set<string>();
			for (const sid of prev) if (visibleIdSet.has(sid)) next.add(sid);
			if (allVisibleSelected) {
				for (const i of visibleItems) next.delete(i.id);
			} else {
				for (const i of visibleItems) next.add(i.id);
			}
			return next;
		});

	const handleUnassign = async () => {
		const ids = Array.from(effectiveSelected);
		if (ids.length === 0) return;
		setSubmitting(true);
		try {
			await onUnassign(ids);
			toast.success(`Removed ${ids.length} ${consumerLabel}`);
			setSelected(new Set());
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : `Failed to remove ${consumerLabel}`,
			);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<div className="flex flex-col gap-3">
			<div className="flex items-center gap-3">
				<SearchBox
					value={search}
					onChange={setSearch}
					placeholder={`Search ${consumerLabel}...`}
					className="flex-1"
				/>
				<Button onClick={() => setDrawerOpen(true)}>
					<Plus className="h-4 w-4 mr-1.5" />
					Assign {consumerLabel}
				</Button>
			</div>

			{isLoading ? (
				<div className="space-y-2">
					{[...Array(4)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : items.length === 0 ? (
				<div className="text-sm text-muted-foreground py-8 text-center border rounded">
					{emptyHint}
				</div>
			) : (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead className="w-0 whitespace-nowrap">
								<Checkbox
									checked={
										allVisibleSelected
											? true
											: someVisibleSelected
												? "indeterminate"
												: false
									}
									onCheckedChange={toggleAll}
									aria-label={`Select all visible ${consumerLabel}`}
								/>
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								{primaryColumnLabel}
							</DataTableHead>
							{!hideSecondary && (
								<DataTableHead>{secondaryColumnLabel}</DataTableHead>
							)}
							{showOrgColumn && (
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
							)}
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{visibleItems.map((item) => (
							<DataTableRow key={item.id} className="group/row">
								<DataTableCell className="w-0 whitespace-nowrap">
									<Checkbox
										checked={effectiveSelected.has(item.id)}
										onCheckedChange={() => toggleOne(item.id)}
										aria-label={`Select ${item.primary}`}
									/>
								</DataTableCell>
								<DataTableCell className="w-0 whitespace-nowrap font-medium">
									{item.primary}
								</DataTableCell>
								{!hideSecondary && (
									<DataTableCell className="max-w-xs truncate text-muted-foreground">
										{item.secondary ? (
											<Tooltip>
												<TooltipTrigger asChild>
													<span className="block truncate">
														{item.secondary}
													</span>
												</TooltipTrigger>
												<TooltipContent>{item.secondary}</TooltipContent>
											</Tooltip>
										) : (
											<span className="text-muted-foreground/60">-</span>
										)}
									</DataTableCell>
								)}
								{showOrgColumn && (
									<DataTableCell className="w-0 whitespace-nowrap text-sm">
										<OrgBadge org={item.org ?? null} />
									</DataTableCell>
								)}
							</DataTableRow>
						))}
					</DataTableBody>
				</DataTable>
			)}

			{effectiveSelected.size > 0 && (
				<div
					role="region"
					aria-label={`Selected ${consumerLabel}`}
					className="sticky bottom-2 flex items-center gap-3 rounded-lg border bg-popover px-4 py-2 shadow-lg"
				>
					<span className="text-sm font-medium">
						{effectiveSelected.size} selected
					</span>
					<Button
						variant="destructive"
						size="sm"
						disabled={submitting}
						onClick={handleUnassign}
					>
						{submitting ? "Unassigning..." : "Unassign from role"}
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="ml-auto"
						onClick={() => setSelected(new Set())}
						aria-label="Clear selection"
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
			)}

			{drawerOpen && (
				<AssignDrawer
					assignedIds={new Set(items.map((i) => i.id))}
					candidates={candidates}
					candidatesLoading={candidatesLoading}
					consumerLabel={consumerLabel}
					showOrgColumn={showOrgColumn}
					onClose={() => setDrawerOpen(false)}
					onAssign={onAssign}
				/>
			)}
		</div>
	);
}

export function OrgBadge({ org }: { org: OrgInfo | null }) {
	if (!org || !org.id) {
		return (
			<span className="inline-flex items-center gap-1 text-muted-foreground">
				<Building2 className="h-3.5 w-3.5" />
				Platform
			</span>
		);
	}
	return (
		<span className="inline-flex items-center gap-1">
			{org.isProvider ? (
				<Star className="h-3.5 w-3.5 text-amber-500 fill-amber-500" />
			) : (
				<Building2 className="h-3.5 w-3.5 text-muted-foreground" />
			)}
			<span className="truncate">{org.name}</span>
		</span>
	);
}

// =============================================================================
// AssignDrawer
// =============================================================================

interface AssignDrawerProps {
	assignedIds: Set<string>;
	candidates: ConsumerTabItem[];
	candidatesLoading: boolean;
	consumerLabel: string;
	showOrgColumn?: boolean;
	onClose: () => void;
	onAssign: (ids: string[]) => Promise<void>;
}

function AssignDrawer({
	assignedIds,
	candidates,
	candidatesLoading,
	consumerLabel,
	showOrgColumn,
	onClose,
	onAssign,
}: AssignDrawerProps) {
	const [search, setSearch] = useState("");
	const [showAssigned, setShowAssigned] = useState(false);
	const [picked, setPicked] = useState<Set<string>>(new Set());
	const [submitting, setSubmitting] = useState(false);

	const filtered = useMemo(() => {
		const q = search.trim().toLowerCase();
		return candidates.filter((c) => {
			const isAssigned = assignedIds.has(c.id);
			if (isAssigned && !showAssigned) return false;
			if (!q) return true;
			return (
				c.primary.toLowerCase().includes(q) ||
				(c.secondary ?? "").toLowerCase().includes(q) ||
				(c.org?.name ?? "").toLowerCase().includes(q)
			);
		});
	}, [candidates, assignedIds, search, showAssigned]);

	const toggle = (id: string) =>
		setPicked((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const handleSubmit = async () => {
		const ids = Array.from(picked).filter((id) => !assignedIds.has(id));
		if (ids.length === 0) {
			toast.error("Select at least one item to assign");
			return;
		}
		setSubmitting(true);
		try {
			await onAssign(ids);
			toast.success(`Assigned ${ids.length} ${consumerLabel}`);
			setPicked(new Set());
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : `Failed to assign ${consumerLabel}`,
			);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<Sheet open onOpenChange={(o) => !o && onClose()}>
			<SheetContent
				side="right"
				className="w-[480px] sm:max-w-[480px] flex flex-col"
			>
				<SheetHeader>
					<SheetTitle>Assign {consumerLabel}</SheetTitle>
					<SheetDescription>
						Pick the {consumerLabel} you want to add to this role.
						Already-assigned entries are hidden by default — toggle the switch
						below to see them.
					</SheetDescription>
				</SheetHeader>

				<div className="px-4 space-y-2">
					<SearchBox
						value={search}
						onChange={setSearch}
						placeholder={`Search ${consumerLabel}...`}
					/>
					<label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
						<Checkbox
							checked={showAssigned}
							onCheckedChange={(v) => setShowAssigned(v === true)}
						/>
						Show already-assigned
					</label>
				</div>

				<div className="flex-1 overflow-y-auto px-4 pb-2">
					{candidatesLoading ? (
						<div className="space-y-2 mt-2">
							{[...Array(6)].map((_, i) => (
								<Skeleton key={i} className="h-10 w-full" />
							))}
						</div>
					) : filtered.length === 0 ? (
						<div className="text-sm text-muted-foreground py-8 text-center">
							No {consumerLabel} available to assign.
						</div>
					) : (
						<div className="border rounded divide-y">
							{filtered.map((c) => {
								const isAssigned = assignedIds.has(c.id);
								return (
									<label
										key={c.id}
										className={
											"flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent/30" +
											(isAssigned ? " opacity-60" : "")
										}
									>
										<Checkbox
											checked={picked.has(c.id)}
											onCheckedChange={() => toggle(c.id)}
											disabled={isAssigned}
											aria-label={`Pick ${c.primary}`}
										/>
										<div className="flex-1 min-w-0">
											<div className="text-sm font-medium truncate">
												{c.primary}
												{isAssigned && (
													<span className="ml-2 text-xs text-muted-foreground">
														(assigned)
													</span>
												)}
											</div>
											{c.secondary && (
												<div className="text-xs text-muted-foreground truncate">
													{c.secondary}
												</div>
											)}
											{showOrgColumn && c.org && (
												<div className="text-xs text-muted-foreground truncate">
													<OrgBadge org={c.org} />
												</div>
											)}
										</div>
									</label>
								);
							})}
						</div>
					)}
				</div>

				<SheetFooter>
					<Button variant="outline" onClick={onClose}>
						Close
					</Button>
					<Button
						disabled={submitting || picked.size === 0}
						onClick={handleSubmit}
					>
						{submitting ? "Assigning..." : `Assign ${picked.size}`}
					</Button>
				</SheetFooter>
			</SheetContent>
		</Sheet>
	);
}
