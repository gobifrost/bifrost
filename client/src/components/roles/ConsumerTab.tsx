import { getErrorMessage } from "@/lib/api-error";
import { ConsumerCards } from "./ConsumerCards";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { useMemo, useState } from "react";
import { Building2, Plus, Star, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableFooter,
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
import { ListPagination } from "@/components/pagination/ListPagination";

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

interface ConsumerReadState {
	isError: boolean;
	isFetching: boolean;
	onRetry: () => void;
}

function ConsumerReadError({
	hasRecords,
	state,
	label,
}: {
	state?: ConsumerReadState;
	hasRecords: boolean;
	label: string;
}) {
	if (!state?.isError) return null;
	return (
		<div
			role="alert"
			className="space-y-2 rounded-[var(--bf-radius-surface)] border p-4 text-sm"
		>
			<p>
				{label} could not load.{" "}
				{hasRecords
					? "Previously loaded records are shown."
					: "Try again to load records for this view."}
			</p>
			<Button
				variant="outline"
				className="min-h-11"
				disabled={state.isFetching}
				onClick={state.onRetry}
			>
				Retry {label.toLowerCase()}
			</Button>
		</div>
	);
}

export interface ConsumerTabProps {
	items: ConsumerTabItem[];
	isLoading: boolean;
	readState?: ConsumerReadState;
	candidates: ConsumerTabItem[];
	candidatesLoading: boolean;
	candidatesReadState?: ConsumerReadState;
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
	searchValue?: string;
	onSearchChange?: (value: string) => void;
	pagination?: {
		offset: number;
		limit: number;
		total: number;
		isFetching?: boolean;
		onPageChange: (offset: number) => void;
	};
	onItemClick?: (item: ConsumerTabItem) => void;
	getItemHref?: (item: ConsumerTabItem) => string;
	onRequestCandidates?: () => void;
	onAssign: (ids: string[]) => Promise<void>;
	onUnassign: (ids: string[]) => Promise<void>;
}

/**
 * Generic role-consumer tab. Renders the assigned items as a standard
 * DataTable (matching /users + /history conventions), with an optional
 * Organization column for entity types that carry an org_id.
 */
export function ConsumerTab({
	items,
	isLoading,
	readState,
	candidates,
	candidatesLoading,
	candidatesReadState,
	consumerLabel,
	emptyHint,
	primaryColumnLabel = "Name",
	secondaryColumnLabel = "Description",
	hideSecondary = false,
	showOrgColumn = false,
	searchValue,
	onSearchChange,
	pagination,
	onItemClick,
	getItemHref,
	onRequestCandidates,
	onAssign,
	onUnassign,
}: ConsumerTabProps) {
	const desktop = useMediaQuery("(min-width: 1024px)");
	const [internalSearch, setInternalSearch] = useState("");
	const search = searchValue ?? internalSearch;
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [removeFailed, setRemoveFailed] = useState<string | null>(null);
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
	const showPagination =
		!!pagination &&
		(items.length > 0 || pagination.offset > 0 || pagination.total > 0);
	const showEmptyPagination =
		showPagination &&
		!isLoading &&
		items.length === 0 &&
		!readState?.isError;

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
		if (submitting) return;
		setRemoveFailed(null);
		const ids = Array.from(effectiveSelected);
		if (ids.length === 0) return;
		setSubmitting(true);
		try {
			await onUnassign(ids);
			toast.success(`Removed ${ids.length} ${consumerLabel}`);
			setSelected(new Set());
		} catch (e) {
			setRemoveFailed(
				getErrorMessage(e, `Failed to remove ${consumerLabel}`),
			);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<div className="flex min-w-0 flex-col gap-3 lg:min-h-0 lg:flex-1">
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center [&_input]:min-h-11 [&>button]:min-h-11">
				<SearchBox
					value={search}
					onChange={onSearchChange ?? setInternalSearch}
					placeholder={`Search ${consumerLabel}...`}
					className="flex-1"
				/>
				<Button
					onClick={() => {
						onRequestCandidates?.();
						setDrawerOpen(true);
					}}
				>
					<Plus className="h-4 w-4 mr-1.5" />
					Assign {consumerLabel}
				</Button>
			</div>

			<ConsumerReadError
				hasRecords={items.length > 0}
				state={readState}
				label={`Assigned ${consumerLabel}`}
			/>
			{isLoading ? (
				<div className="space-y-2">
					{[...Array(4)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : readState?.isError &&
			  items.length === 0 ? null : items.length === 0 ? (
				<div className="text-sm text-muted-foreground py-8 text-center rounded-lg ring-1 ring-foreground/5">
					{emptyHint}
				</div>
			) : (
				<div className="flex-1 min-h-0">
					{!desktop ? (
						<>
							<ConsumerCards
								items={visibleItems}
								selected={effectiveSelected}
								label={consumerLabel}
								allSelected={allVisibleSelected}
								someSelected={someVisibleSelected}
								pending={submitting}
								hideSecondary={hideSecondary}
								showOrg={showOrgColumn}
								onToggle={toggleOne}
								onToggleAll={toggleAll}
								onItemClick={onItemClick}
								getItemHref={getItemHref}
							/>
							{showPagination && pagination && (
								<ListPagination {...pagination} />
							)}
						</>
					) : (
						<DataTable className="max-h-full">
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
											disabled={submitting}
											onCheckedChange={toggleAll}
											aria-label={`Select all visible ${consumerLabel}`}
										/>
									</DataTableHead>
									{showOrgColumn && (
										<DataTableHead className="w-0 whitespace-nowrap">
											Organization
										</DataTableHead>
									)}
									<DataTableHead className="w-0 whitespace-nowrap">
										{primaryColumnLabel}
									</DataTableHead>
									{!hideSecondary && (
										<DataTableHead>
											{secondaryColumnLabel}
										</DataTableHead>
									)}
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{visibleItems.map((item) => (
									<DataTableRow
										key={item.id}
										className="group/row"
										clickable={Boolean(onItemClick)}
										href={getItemHref?.(item)}
										onClick={() => onItemClick?.(item)}
									>
										<DataTableCell
											className="w-0 whitespace-nowrap"
											onClick={(event) =>
												event.stopPropagation()
											}
										>
											<Checkbox
												checked={effectiveSelected.has(
													item.id,
												)}
												onCheckedChange={() =>
													toggleOne(item.id)
												}
												disabled={submitting}
												aria-label={`Select ${item.primary}`}
											/>
										</DataTableCell>
										{showOrgColumn && (
											<DataTableCell className="w-0 whitespace-nowrap text-sm">
												<OrgBadge
													org={item.org ?? null}
												/>
											</DataTableCell>
										)}
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
														<TooltipContent>
															{item.secondary}
														</TooltipContent>
													</Tooltip>
												) : (
													<span className="text-muted-foreground/60">
														-
													</span>
												)}
											</DataTableCell>
										)}
									</DataTableRow>
								))}
							</DataTableBody>
							{showPagination && pagination && (
								<DataTableFooter>
									<DataTableRow>
										<DataTableCell
											colSpan={
												2 +
												(showOrgColumn ? 1 : 0) +
												(hideSecondary ? 0 : 1)
											}
											className="p-0"
										>
											<ListPagination {...pagination} />
										</DataTableCell>
									</DataTableRow>
								</DataTableFooter>
							)}
						</DataTable>
					)}
				</div>
			)}
			{showEmptyPagination && pagination && (
				<ListPagination {...pagination} />
			)}

			{effectiveSelected.size > 0 && (
				<div
					role="region"
					aria-label={`Selected ${consumerLabel}`}
					className="sticky bottom-2 grid grid-cols-[1fr_auto] items-center gap-3 sm:flex sm:flex-wrap rounded-[var(--bf-radius-surface)] bg-popover px-4 py-2 shadow-lg ring-1 ring-foreground/5 dark:ring-foreground/10"
				>
					{removeFailed && (
						<p
							role="alert"
							className="order-0 col-span-2 w-full text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{removeFailed}. Your selection is preserved. Try
							again.
						</p>
					)}
					<span className="order-1 text-sm font-medium">
						{effectiveSelected.size} selected
					</span>
					<Button
						variant="destructive"
						className="order-3 col-span-2 min-h-11 w-full sm:order-2 sm:w-auto"
						size="sm"
						disabled={submitting}
						onClick={handleUnassign}
					>
						{submitting ? "Unassigning..." : "Unassign from role"}
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="order-2 ml-auto min-h-11 sm:order-3"
						disabled={submitting}
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
					candidatesReadState={candidatesReadState}
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
	candidatesReadState?: ConsumerReadState;
	consumerLabel: string;
	showOrgColumn?: boolean;
	onClose: () => void;
	onAssign: (ids: string[]) => Promise<void>;
}

function AssignDrawer({
	assignedIds,
	candidates,
	candidatesLoading,
	candidatesReadState,
	consumerLabel,
	showOrgColumn,
	onClose,
	onAssign,
}: AssignDrawerProps) {
	const returnFocus = useDialogReturnFocus();
	const [failed, setFailed] = useState(false);
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
		if (submitting) return;
		setFailed(false);
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
			setFailed(true);
			toast.error(
				e instanceof Error
					? e.message
					: `Failed to assign ${consumerLabel}`,
			);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<Sheet open onOpenChange={(o) => !o && !submitting && onClose()}>
			<SheetContent
				side="right"
				{...returnFocus}
				onEscapeKeyDown={(event) => {
					if (submitting) event.preventDefault();
				}}
				onInteractOutside={(event) => {
					if (submitting) event.preventDefault();
				}}
				className="w-full sm:max-w-[480px] flex min-w-0 flex-col"
			>
				<SheetHeader>
					<SheetTitle>Assign {consumerLabel}</SheetTitle>
					<SheetDescription>
						Pick the {consumerLabel} you want to add to this role.
						Already-assigned entries are hidden by default — toggle
						the switch below to see them.
					</SheetDescription>
				</SheetHeader>

				<div className="px-4 sm:px-6 pb-4 space-y-2 [&_input]:min-h-11">
					<SearchBox
						value={search}
						onChange={setSearch}
						placeholder={`Search ${consumerLabel}...`}
					/>
					<label className="flex min-h-11 items-center gap-2 text-sm text-muted-foreground cursor-pointer">
						<Checkbox
							checked={showAssigned}
							onCheckedChange={(v) => setShowAssigned(v === true)}
						/>
						Show already-assigned
					</label>
				</div>

				<div className="min-h-0 flex-1 overflow-y-auto px-4 sm:px-6 pb-2">
					<ConsumerReadError
						hasRecords={candidates.length > 0}
						state={candidatesReadState}
						label={`Available ${consumerLabel}`}
					/>
					{candidatesLoading ? (
						<div className="space-y-2 mt-2">
							{[...Array(6)].map((_, i) => (
								<Skeleton key={i} className="h-10 w-full" />
							))}
						</div>
					) : candidatesReadState?.isError &&
					  candidates.length === 0 ? null : filtered.length === 0 ? (
						<div className="text-sm text-muted-foreground py-8 text-center">
							No {consumerLabel} available to assign.
						</div>
					) : (
						<div className="overflow-hidden rounded-lg ring-1 ring-foreground/5 divide-y">
							{filtered.map((c) => {
								const isAssigned = assignedIds.has(c.id);
								return (
									<label
										key={c.id}
										className={
											"flex min-h-11 items-start gap-3 px-3 py-3 cursor-pointer hover:bg-accent/30" +
											(isAssigned ? " opacity-60" : "")
										}
									>
										<Checkbox
											checked={picked.has(c.id)}
											onCheckedChange={() => toggle(c.id)}
											disabled={isAssigned || submitting}
											aria-label={`Pick ${c.primary}`}
										/>
										<div className="flex-1 min-w-0">
											<div className="text-sm font-medium [overflow-wrap:anywhere]">
												{c.primary}
												{isAssigned && (
													<span className="ml-2 text-xs text-muted-foreground">
														(assigned)
													</span>
												)}
											</div>
											{c.secondary && (
												<div className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
													{c.secondary}
												</div>
											)}
											{showOrgColumn && c.org && (
												<div className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
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

				{failed && (
					<p
						role="alert"
						className="mx-4 rounded-[var(--bf-radius-control)] bg-[var(--bf-warning-soft)] p-3 text-sm"
					>
						Could not assign the selected items. Your selection is
						preserved. Try again.
					</p>
				)}
				<SheetFooter>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={submitting}
						onClick={onClose}
					>
						Close
					</Button>
					<Button
						className="min-h-11"
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
