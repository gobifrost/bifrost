import { useMemo } from "react";
import { Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import type { WorkspaceBundlePreview } from "@/services/solutions";

type Decision = "keep" | "replace";
type Item = WorkspaceBundlePreview["items"][number];

const kindLabel: Record<string, string> = {
	app: "APP",
	workflow: "WF",
	table: "TBL",
	form: "FRM",
	agent: "AGT",
	file: "FILE",
	config: "CFG",
	event: "EVT",
	integration: "INT",
	file_policy: "POL",
	policy_rule: "POL",
};

interface DisplayGroup {
	key: string;
	/** First member in preview order; carries the group's decision control. */
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
}: {
	preview: WorkspaceBundlePreview;
	decisions: Record<string, Decision>;
	onDecisionsChange: (decisions: Record<string, Decision>) => void;
}) {
	const conflicts = useMemo(
		() =>
			preview.items.filter((item) => item.classification === "conflict"),
		[preview.items],
	);
	const groups = useMemo(() => groupItems(preview.items), [preview.items]);
	const resolved = conflicts.filter((item) => decisions[item.id]).length;
	const warnings = preview.warnings ?? [];
	const groupConflicts = (group: DisplayGroup) =>
		group.members.filter((item) => item.classification === "conflict");
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
		<div className="min-h-0">
			<div className="flex gap-2 px-5 pt-4 text-xs text-muted-foreground">
				<Info className="mt-0.5 size-4 shrink-0" />
				<div>
					<p>
						Solutions are designed to work together. Replacing items
						in your workspace will overwrite local changes that
						could be important to other things in your workspace,
						and likewise keeping them could materially affect how
						things in this Solution work together.
					</p>
					{warnings.map((warning) => (
						<p key={warning} className="mt-1">
							{warning}
						</p>
					))}
				</div>
			</div>
			<div className="flex min-h-12 flex-wrap items-center justify-between gap-2 px-5 py-2">
				<p className="text-sm">
					<span className="font-semibold">
						{conflicts.length - resolved} need review
					</span>
					<span className="ml-2 text-muted-foreground">
						of {preview.items.length} changes
					</span>
				</p>
				<div className="flex gap-2">
					<Button
						type="button"
						size="sm"
						variant="outline"
						onClick={() => chooseAll("keep")}
					>
						Keep all
					</Button>
					<Button
						type="button"
						size="sm"
						variant="outline"
						onClick={() => chooseAll("replace")}
					>
						Replace all
					</Button>
				</div>
			</div>
			<div className="min-h-0 px-5 pb-5">
				<DataTable
					data-testid="workspace-import-scroller"
					className="max-h-[52dvh]"
				>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead>Item</DataTableHead>
							<DataTableHead>Matched by</DataTableHead>
							<DataTableHead className="text-right">Decision</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{groups.map((group) => (
							<ImportGroupRows
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

function ImportGroupRows({
	group,
	decisions,
	onDecision,
}: {
	group: DisplayGroup;
	decisions: Record<string, Decision>;
	onDecision: (action: Decision) => void;
}) {
	const open = group.members.filter(
		(item) => item.classification === "conflict",
	);
	const decided = open.filter((item) => decisions[item.id]);
	return (
		<>
			{group.members.map((item) =>
				item === group.first ? (
					<ImportRow
						key={item.id}
						item={item}
						openCount={open.length}
						decidedCount={decided.length}
						decision={decisions[item.id]}
						showControl={open.length > 0}
						onDecision={onDecision}
					/>
				) : (
					<ImportRow
						key={item.id}
						item={item}
						linkedTo={group.first.name}
						decision={decisions[item.id]}
						showControl={false}
						onDecision={onDecision}
					/>
				),
			)}
		</>
	);
}

function ImportRow({
	item,
	decision,
	linkedTo,
	showControl,
	openCount,
	decidedCount,
	onDecision,
}: {
	item: Item;
	decision?: Decision;
	linkedTo?: string;
	showControl: boolean;
	openCount?: number;
	decidedCount?: number;
	onDecision: (action: Decision) => void;
}) {
	const conflict = item.classification === "conflict";
	return (
		<DataTableRow>
			<DataTableCell className="min-w-0">
				<span className="flex min-w-0 items-start gap-2">
					<span
						className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-md text-[10px] font-bold ${item.classification === "create" ? "bg-emerald-500/10 text-emerald-700" : "bg-primary/10 text-primary"}`}
					>
						{kindLabel[item.kind] ?? item.kind.slice(0, 4).toUpperCase()}
					</span>
					<span className="min-w-0">
						<span className="block break-words font-medium">
							{item.name}
						</span>
						{item.target_id && (
							<span className="mt-0.5 block break-all font-mono text-[11px] text-muted-foreground">
								Preserves destination ID {item.target_id}
							</span>
						)}
					</span>
				</span>
			</DataTableCell>
			<DataTableCell className="min-w-28 max-w-64 break-words text-xs text-muted-foreground">
				{item.match_key ??
					(item.classification === "create" ? "New" : "—")}
			</DataTableCell>
			<DataTableCell className="text-right">
				{showControl ? (
					<span className="inline-flex items-center gap-2">
						{openCount !== undefined && openCount > 1 && (
							<span className="text-xs text-muted-foreground">
								{decidedCount} of {openCount} decided
							</span>
						)}
						<span className="inline-grid grid-cols-2 rounded-md bg-muted p-0.5 text-xs">
							<DecisionButton
								selected={decision === "keep"}
								onClick={() => onDecision("keep")}
							>
								Keep
							</DecisionButton>
							<DecisionButton
								selected={decision === "replace"}
								onClick={() => onDecision("replace")}
							>
								Replace
							</DecisionButton>
						</span>
					</span>
				) : conflict ? (
					<span className="inline-block max-w-56 break-words text-xs text-muted-foreground">
						Same as {linkedTo}
					</span>
				) : (
					<span
						className={`text-xs font-medium ${item.classification === "create" ? "text-emerald-700" : "text-muted-foreground"}`}
					>
						{item.classification === "create" ? "Create" : "Unchanged"}
					</span>
				)}
			</DataTableCell>
		</DataTableRow>
	);
}

function DecisionButton({
	selected,
	onClick,
	children,
}: {
	selected: boolean;
	onClick: () => void;
	children: string;
}) {
	return (
		<button
			type="button"
			aria-pressed={selected}
			onClick={onClick}
			className={`rounded px-2 py-1 font-medium ${selected ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
		>
			{children}
		</button>
	);
}
