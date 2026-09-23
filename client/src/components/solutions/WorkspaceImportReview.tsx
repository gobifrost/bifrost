import { useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ENTITY_CONFIG } from "@/components/entity-management/types";
import { cn } from "@/lib/utils";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import type { WorkspaceBundlePreview } from "@/services/solutions";
import { useInstallSession } from "./InstallSession";

type Decision = "keep" | "replace";
type Item = WorkspaceBundlePreview["items"][number];

// Type language shared with Entity Management: full-word labels in the same
// outline badge. Kinds Entity Management doesn't manage (files, configs,
// events, policies, claims, integrations) use the same shape, neutrally.
const EXTRA_KIND_CONFIG = {
	table: { label: "Table" },
	config: { label: "Config" },
	event: { label: "Event" },
	file: { label: "File" },
	integration: { label: "Integration" },
	file_policy: { label: "Policy" },
	policy_rule: { label: "Policy" },
	claim: { label: "Claim" },
} as const;

function TypeBadge({ kind }: { kind: string }) {
	const shared = (ENTITY_CONFIG as Record<string, { label: string; color: string }>)[kind];
	if (shared) {
		return (
			<Badge variant="outline" className={cn("h-5 w-24 shrink-0 justify-center px-1.5", shared.color)}>
				{shared.label}
			</Badge>
		);
	}
	const extra = (EXTRA_KIND_CONFIG as Record<string, { label: string }>)[kind];
	return (
		<Badge variant="outline" className="h-5 w-24 shrink-0 justify-center px-1.5">
			{extra?.label ?? kind}
		</Badge>
	);
}

interface DisplayGroup {
	key: string;
	/** First member in preview order; names the row and carries its control. */
	first: Item;
	members: Item[];
}

function groupItems(items: Item[]): DisplayGroup[] {
	const byKey = new Map<string, Item[]>();
	const solo: Item[] = [];
	for (const item of items) {
		if (item.group_key) {
			const list = byKey.get(item.group_key) ?? [];
			list.push(item);
			byKey.set(item.group_key, list);
		} else {
			solo.push(item);
		}
	}
	const emitted = new Set<string>();
	const groups: DisplayGroup[] = [];
	const takeGroup = (key: string): DisplayGroup | null => {
		const members = byKey.get(key);
		if (!members || emitted.has(key)) return null;
		emitted.add(key);
		return { key, first: members[0], members };
	};
	// Definitions first in preview order, each followed by their files, so a
	// definition and the source implementing it are always decided together.
	for (const item of items) {
		if (item.kind === "file" || !item.group_key) continue;
		const group = takeGroup(item.group_key);
		if (group) groups.push(group);
	}
	for (const item of solo) {
		groups.push({ key: item.id, first: item, members: [item] });
	}
	// Defensive: grouped files whose definition is absent stay usable alone.
	for (const [key, members] of byKey) {
		if (!emitted.has(key)) {
			emitted.add(key);
			for (const member of members) {
				groups.push({ key: member.id, first: member, members: [member] });
			}
		}
	}
	return groups;
}

export function WorkspaceImportReview({
	preview,
	decisions,
	onDecisionsChange,
	configuration,
}: {
	preview: WorkspaceBundlePreview;
	decisions: Record<string, Decision>;
	onDecisionsChange: (decisions: Record<string, Decision>) => void;
	configuration?: ReactNode;
}) {
	const reviewRef = useRef<HTMLDivElement>(null);
	const { setTall } = useInstallSession();
	useLayoutEffect(() => {
		const review = reviewRef.current;
		const fieldset = review?.parentElement;
		const dialog = review?.closest<HTMLElement>("[data-testid=solution-dialog]");
		if (!review || !fieldset || !dialog) return;
		let frame = 0;
		const measureContent = () => {
			cancelAnimationFrame(frame);
			frame = requestAnimationFrame(() => {
				const table = review.querySelector("table");
				const list = review.lastElementChild as HTMLElement | null;
				if (!table || !list) return;
				const fixedHeight = [...fieldset.children]
					.filter((child) => child !== review)
					.reduce((height, child) => height + (child as HTMLElement).offsetHeight, 0);
				const reviewHeaderHeight = [...review.children]
					.slice(0, -1)
					.reduce((height, child) => height + (child as HTMLElement).offsetHeight, 0);
				const listStyle = getComputedStyle(list);
				const listPadding = parseFloat(listStyle.paddingTop) + parseFloat(listStyle.paddingBottom);
				const naturalHeight = fixedHeight + reviewHeaderHeight + listPadding + table.getBoundingClientRect().height + 4;
				const constrained = naturalHeight > window.innerHeight * 0.9;
				// Fieldset's flex child can retain its intrinsic table height even
				// after the dialog itself is capped. Give that child an explicit cap.
				review.style.maxHeight = constrained
					? `${Math.max(0, window.innerHeight * 0.9 - fixedHeight - 4)}px`
					: "";
				setTall(constrained);
			});
		};
		measureContent();
		window.addEventListener("resize", measureContent);
		return () => {
			cancelAnimationFrame(frame);
			window.removeEventListener("resize", measureContent);
			review.style.maxHeight = "";
		};
	}, [preview.items, configuration, setTall]);
	useEffect(() => () => setTall(false), [setTall]);
	const conflicts = useMemo(
		() =>
			preview.items.filter((item) => item.classification === "conflict"),
		[preview.items],
	);
	const groups = useMemo(() => groupItems(preview.items), [preview.items]);
	const groupConflicts = (group: DisplayGroup) =>
		group.members.filter((item) => item.classification === "conflict");
	const reviewGroups = groups.filter((group) => groupConflicts(group).length > 0);
	const remainingGroups = reviewGroups.filter((group) =>
		groupConflicts(group).some((item) => !decisions[item.id]),
	).length;
	const chooseGroup = (group: DisplayGroup, action: Decision) =>
		onDecisionsChange({
			...decisions,
			...Object.fromEntries(
				groupConflicts(group).map((item) => [item.id, action]),
			),
		});
	const chooseAll = (action: Decision) =>
		onDecisionsChange(
			Object.fromEntries(conflicts.map((item) => [item.id, action])),
		);

	return (
		<div ref={reviewRef} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden max-sm:overflow-y-auto">
			<div className="flex min-w-0 shrink-0 gap-2 px-5 pt-4 text-sm text-foreground/80">
				<Info className="mt-0.5 size-4 shrink-0 text-primary" />
				<div className="min-w-0">
					<p className="break-words">
						Keep and Replace decisions can affect other workspace content.
						Review each conflict before importing.
					</p>
				</div>
			</div>
			{configuration}
			<div className="flex min-h-12 shrink-0 flex-wrap items-center justify-between gap-2 px-5 py-2">
				<p className="text-sm">
					<span className="font-semibold">
						{remainingGroups === 0
							? "All reviewed"
							: `${remainingGroups} ${remainingGroups === 1 ? "item needs" : "items need"} review`}
					</span>
					<span className="ml-2 text-muted-foreground">
						of {groups.length} items
					</span>
				</p>
				<span className="inline-grid grid-cols-2 rounded-md bg-muted p-0.5 text-xs">
					<DecisionButton
						selected={false}
						disabled={conflicts.length === 0}
						onClick={() => chooseAll("keep")}
					>
						Keep All
					</DecisionButton>
					<DecisionButton
						selected={false}
						disabled={conflicts.length === 0}
						onClick={() => chooseAll("replace")}
					>
						Replace All
					</DecisionButton>
				</span>
			</div>
			<div className="flex min-h-0 min-w-0 flex-1 flex-col px-5 pb-5 max-sm:flex-none">
				<DataTable
					data-testid="workspace-import-scroller"
					className="min-h-0 flex-1 max-sm:max-h-none max-sm:flex-none"
				>
					<DataTableHeader className="max-sm:hidden">
						<DataTableRow>
							<DataTableHead>Item</DataTableHead>
							<DataTableHead className="w-32 text-right sm:w-44">Decision</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{groups.map((group) => (
							<ImportGroupRow
								key={group.key}
								group={group}
								decisions={decisions}
								onDecision={(action) => chooseGroup(group, action)}
							/>
						))}
					</DataTableBody>
				</DataTable>
			</div>
		</div>
	);
}

function ImportGroupRow({
	group,
	decisions,
	onDecision,
}: {
	group: DisplayGroup;
	decisions: Record<string, Decision>;
	onDecision: (action: Decision) => void;
}) {
	const { first, members } = group;
	const open = members.filter(
		(item) => item.classification === "conflict",
	);
	// One control per group: it decides every open conflict in the group, so
	// a definition and its files can never be split across Keep/Replace. The
	// control reads from the first open member; the group moves as one.
	const controlDecision = open.length > 0 ? decisions[open[0].id] : undefined;
	const extraDefinitions = members.filter(
		(member) => member !== first && member.kind !== "file",
	);
	return (
		<DataTableRow className="max-sm:grid max-sm:grid-cols-1">
			<DataTableCell className="min-w-0">
				<span className="flex min-w-0 items-start gap-2">
					<TypeBadge kind={first.kind} />
					<span className="min-w-0">
						<span className="block break-all font-medium">
							{first.name}
						</span>
						{first.scope_change && <span className="block text-xs text-muted-foreground">Replace moves this item to the selected scope</span>}
						{first.kind === "table" && first.classification === "conflict" && (
							<span className="block text-xs text-muted-foreground">
								Replace updates the table definition and access policies; existing rows stay.
							</span>
						)}
						{extraDefinitions.map((definition) => (
							<span
								key={definition.id}
								className="mt-0.5 block break-all text-xs text-muted-foreground"
							>
								Also covers {definition.name}
							</span>
						))}
					</span>
				</span>
			</DataTableCell>
			<DataTableCell className="w-32 text-right max-sm:flex max-sm:w-full max-sm:justify-end max-sm:pt-0 sm:w-44">
				{open.length > 0 ? (
					<span className="inline-grid grid-cols-2 rounded-md bg-muted p-0.5 text-xs">
						<DecisionButton
							selected={controlDecision === "keep"}
							onClick={() => onDecision("keep")}
						>
							Keep
						</DecisionButton>
						<DecisionButton
							selected={controlDecision === "replace"}
							onClick={() => onDecision("replace")}
						>
							Replace
						</DecisionButton>
					</span>
				) : (
					<span
						className={`text-xs font-medium ${first.classification === "create" ? "text-emerald-700" : "text-muted-foreground"}`}
					>
						{first.classification === "create" ? "Create" : "Unchanged"}
					</span>
				)}
			</DataTableCell>
		</DataTableRow>
	);
}

function DecisionButton({
	selected,
	disabled,
	onClick,
	children,
}: {
	selected: boolean;
	disabled?: boolean;
	onClick: () => void;
	children: string;
}) {
	return (
		<button
			type="button"
			aria-pressed={selected}
			disabled={disabled}
			onClick={onClick}
			className={`rounded-none px-2 py-1 font-medium first:rounded-l last:rounded-r disabled:cursor-not-allowed disabled:opacity-50 ${selected ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
		>
			{children}
		</button>
	);
}
