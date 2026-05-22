import { useMemo, useState } from "react";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { SearchBox } from "@/components/search/SearchBox";

export interface ConsumerTabItem {
	id: string;
	primary: string;
	secondary?: string | null;
}

export interface ConsumerTabProps {
	/** Items currently assigned to the role (left side, with checkboxes). */
	items: ConsumerTabItem[];
	isLoading: boolean;
	/** All items that COULD be assigned (drives the drawer). */
	candidates: ConsumerTabItem[];
	/** True when candidates are loading — used by drawer. */
	candidatesLoading: boolean;
	consumerLabel: string;
	emptyHint: string;
	onAssign: (ids: string[]) => Promise<void>;
	onUnassign: (ids: string[]) => Promise<void>;
}

/**
 * Generic role-consumer tab. Lists assigned items with a checkbox + search,
 * sticky "Unassign N" footer when rows are selected, and an "Add" button
 * that opens an AssignDrawer for picking unassigned candidates.
 *
 * Knowledge is the special case — see KnowledgeTab.tsx for the per-row
 * namespace+org shape; the rest of the consumer types (users/forms/agents/
 * apps/workflows) all fit this generic surface.
 */
export function ConsumerTab({
	items,
	isLoading,
	candidates,
	candidatesLoading,
	consumerLabel,
	emptyHint,
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
				(it.secondary ?? "").toLowerCase().includes(q),
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
				<div className="border rounded divide-y">
					<div className="flex items-center gap-3 px-3 py-2 bg-muted/30">
						<Checkbox
							checked={
								allVisibleSelected
									? true
									: effectiveSelected.size > 0
										? "indeterminate"
										: false
							}
							onCheckedChange={toggleAll}
							aria-label={`Select all visible ${consumerLabel}`}
						/>
						<span className="text-xs text-muted-foreground">
							{effectiveSelected.size > 0
								? `${effectiveSelected.size} selected`
								: `${visibleItems.length} ${consumerLabel}`}
						</span>
					</div>
					{visibleItems.map((item) => (
						<label
							key={item.id}
							className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent/30"
						>
							<Checkbox
								checked={effectiveSelected.has(item.id)}
								onCheckedChange={() => toggleOne(item.id)}
								aria-label={`Select ${item.primary}`}
							/>
							<div className="flex-1 min-w-0">
								<div className="text-sm font-medium truncate">
									{item.primary}
								</div>
								{item.secondary && (
									<div className="text-xs text-muted-foreground truncate">
										{item.secondary}
									</div>
								)}
							</div>
						</label>
					))}
				</div>
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
						{submitting ? "Unassigning..." : `Unassign from role`}
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
					onClose={() => setDrawerOpen(false)}
					onAssign={onAssign}
				/>
			)}
		</div>
	);
}

// =============================================================================
// AssignDrawer — picks unassigned candidates and posts them in one batch.
// =============================================================================

import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";

interface AssignDrawerProps {
	assignedIds: Set<string>;
	candidates: ConsumerTabItem[];
	candidatesLoading: boolean;
	consumerLabel: string;
	onClose: () => void;
	onAssign: (ids: string[]) => Promise<void>;
}

function AssignDrawer({
	assignedIds,
	candidates,
	candidatesLoading,
	consumerLabel,
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
				(c.secondary ?? "").toLowerCase().includes(q)
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
			<SheetContent side="right" className="w-[480px] sm:max-w-[480px] flex flex-col">
				<SheetHeader>
					<SheetTitle>Assign {consumerLabel}</SheetTitle>
					<SheetDescription>
						Pick the {consumerLabel} you want to add to this role. Already-assigned
						entries are hidden by default — toggle the switch below to see them.
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
