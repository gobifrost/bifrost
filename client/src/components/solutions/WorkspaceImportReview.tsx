import { useMemo } from "react";
import { Info } from "lucide-react";
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
	claim: "CLM",
};

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
					{warnings.length > 0 && (
						<details>
							<summary className="mt-1 cursor-pointer hover:text-foreground">
								Package notices ({warnings.length})
							</summary>
							{warnings.map((warning) => (
								<p key={warning} className="mt-1">
									{warning}
								</p>
							))}
						</details>
					)}
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
				<span className="inline-grid grid-cols-2 rounded-md bg-muted p-0.5 text-xs">
					<DecisionButton
						selected={false}
						onClick={() => chooseAll("keep")}
					>
						Keep all
					</DecisionButton>
					<DecisionButton
						selected={false}
						onClick={() => chooseAll("replace")}
					>
						Replace all
					</DecisionButton>
				</span>
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
	const files = members.filter(
		(member) => member !== first && member.kind === "file",
	);
	const extraDefinitions = members.filter(
		(member) => member !== first && member.kind !== "file",
	);
	return (
		<DataTableRow>
			<DataTableCell className="min-w-0">
				<span className="flex min-w-0 items-start gap-2">
					<span
						className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-md text-[10px] font-bold ${first.classification === "create" ? "bg-emerald-500/10 text-emerald-700" : "bg-primary/10 text-primary"}`}
					>
						{kindLabel[first.kind] ?? first.kind.slice(0, 4).toUpperCase()}
					</span>
					<span className="min-w-0">
						<span className="block break-words font-medium">
							{first.name}
						</span>
						{files.map((file) => (
							<span
								key={file.id}
								className="mt-0.5 block break-all text-xs text-muted-foreground"
							>
								{file.name}
							</span>
						))}
						{extraDefinitions.map((definition) => (
							<span
								key={definition.id}
								className="mt-0.5 block break-words text-xs text-muted-foreground"
							>
								Also covers {definition.name}
							</span>
						))}
						{first.target_id && (
							<span className="mt-0.5 block break-all font-mono text-[11px] text-muted-foreground">
								Preserves destination ID {first.target_id}
							</span>
						)}
					</span>
				</span>
			</DataTableCell>
			<DataTableCell className="min-w-28 max-w-64 break-words text-xs text-muted-foreground">
				{first.match_key ??
					(first.classification === "create" ? "New" : "—")}
			</DataTableCell>
			<DataTableCell className="text-right">
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
			className={`rounded-none px-2 py-1 font-medium first:rounded-l last:rounded-r ${selected ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
		>
			{children}
		</button>
	);
}
