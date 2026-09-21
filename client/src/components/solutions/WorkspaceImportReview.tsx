import { useMemo, useState } from "react";
import { AlertTriangle, FileCode2, GitCompareArrows } from "lucide-react";
import { Button } from "@/components/ui/button";
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
	const [selectedId, setSelectedId] = useState(
		conflicts[0]?.id ?? preview.items[0]?.id ?? null,
	);
	const selected =
		preview.items.find((item) => item.id === selectedId) ?? null;
	const resolved = conflicts.filter((item) => decisions[item.id]).length;
	const warnings = preview.warnings ?? [];
	const choose = (id: string, action: Decision) =>
		onDecisionsChange({ ...decisions, [id]: action });
	const chooseAll = (action: Decision) =>
		onDecisionsChange(
			Object.fromEntries(conflicts.map((item) => [item.id, action])),
		);

	return (
		<div className="min-h-0">
			<div className="mx-5 mt-4 flex gap-2 rounded-lg border border-amber-500/35 bg-amber-500/5 p-3 text-xs text-muted-foreground">
				<AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
				<div>
					<p>
						<span className="font-semibold text-foreground">
							Review compatibility.
						</span>{" "}
						Package definitions were designed together; mixing kept
						and replaced items can change references. Validate the
						workspace before committing.
					</p>
					{warnings.map((warning) => (
						<p key={warning} className="mt-1">
							{warning}
						</p>
					))}
				</div>
			</div>
			<div
				data-testid="workspace-import-scroller"
				className="min-h-0 max-h-[58dvh] overflow-y-auto border-y md:grid md:grid-cols-[minmax(0,1.45fr)_minmax(20rem,.75fr)]"
			>
				<section className="min-w-0 border-b md:border-b-0 md:border-r">
					<div className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b bg-muted/30 px-3 py-2">
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
					<div className="hidden grid-cols-[2rem_minmax(9rem,1fr)_6rem_10rem] gap-2 border-b px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground md:grid">
						<span />
						<span>Item</span>
						<span>Matched by</span>
						<span>Decision</span>
					</div>
					<div>
						{preview.items.map((item) => (
							<ImportRow
								key={item.id}
								item={item}
								active={selectedId === item.id}
								decision={decisions[item.id]}
								onSelect={() => setSelectedId(item.id)}
								onDecision={(action) => choose(item.id, action)}
							/>
						))}
					</div>
				</section>
				<ImportInspector
					item={selected}
					decision={selected ? decisions[selected.id] : undefined}
					onDecision={(action) =>
						selected && choose(selected.id, action)
					}
				/>
			</div>
		</div>
	);
}

function ImportRow({
	item,
	active,
	decision,
	onSelect,
	onDecision,
}: {
	item: Item;
	active: boolean;
	decision?: Decision;
	onSelect: () => void;
	onDecision: (action: Decision) => void;
}) {
	const conflict = item.classification === "conflict";
	return (
		<div
			className={`grid min-h-14 grid-cols-[2rem_minmax(0,1fr)] gap-x-2 border-b px-3 py-2 text-sm md:grid-cols-[2rem_minmax(9rem,1fr)_6rem_10rem] ${active ? "bg-primary/5 shadow-[inset_3px_0_0_hsl(var(--primary))]" : "hover:bg-muted/40"}`}
		>
			<button
				type="button"
				aria-label={`Inspect ${item.name}`}
				onClick={onSelect}
				className={`grid size-7 place-items-center rounded-md text-[10px] font-bold ${item.classification === "create" ? "bg-emerald-500/10 text-emerald-700" : "bg-primary/10 text-primary"}`}
			>
				{kindLabel[item.kind] ?? item.kind.slice(0, 4).toUpperCase()}
			</button>
			<button
				type="button"
				onClick={onSelect}
				className="min-w-0 text-left"
			>
				<span className="block truncate font-medium">{item.name}</span>
				<span className="block truncate text-xs text-muted-foreground">
					{item.classification === "create"
						? "New workspace content"
						: item.classification === "unchanged"
							? "No definition change"
							: item.id}
				</span>
			</button>
			<span
				title={item.match_key ?? undefined}
				className="col-start-2 mt-1 min-w-0 truncate text-xs text-muted-foreground md:col-start-auto md:mt-0"
			>
				{item.match_key ??
					(item.classification === "create" ? "New" : "—")}
			</span>
			{conflict ? (
				<div className="col-start-2 mt-2 grid grid-cols-2 rounded-md bg-muted p-0.5 text-xs md:col-start-auto md:mt-0">
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
				</div>
			) : (
				<span
					className={`col-start-2 mt-1 text-xs font-medium md:col-start-auto md:mt-0 ${item.classification === "create" ? "text-emerald-700" : "text-muted-foreground"}`}
				>
					{item.classification === "create" ? "Create" : "Unchanged"}
				</span>
			)}
		</div>
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

function ImportInspector({
	item,
	decision,
	onDecision,
}: {
	item: Item | null;
	decision?: Decision;
	onDecision: (action: Decision) => void;
}) {
	if (!item)
		return (
			<aside className="p-5 text-sm text-muted-foreground">
				Select an import item to inspect it.
			</aside>
		);
	const conflict = item.classification === "conflict";
	const diff = item.diff ?? [];
	return (
		<aside className="min-w-0 bg-muted/20 p-5">
			<div className="flex min-w-0 items-start justify-between gap-2">
				<div className="min-w-0">
					<p className="text-[11px] font-semibold uppercase tracking-wide text-primary">
						{item.kind}
					</p>
					<h3 className="mt-1 break-words font-semibold">
						{item.name}
					</h3>
					<p className="mt-1 break-words text-xs text-muted-foreground">
						{item.match_key
							? `Matched by ${item.match_key}`
							: "New workspace content"}
					</p>
				</div>
				<GitCompareArrows className="size-4 shrink-0 text-muted-foreground" />
			</div>
			{item.target_id && (
				<p className="mt-4 break-words rounded-md bg-primary/5 p-2 text-xs text-muted-foreground">
					Replacing preserves destination ID{" "}
					<span className="break-all font-mono">
						{item.target_id}
					</span>
					.
				</p>
			)}
			{diff.length > 0 ? (
				<div className="mt-4 overflow-hidden rounded-md border bg-background font-mono text-xs">
					{diff.map((line) => (
						<div
							key={line.field}
							className="border-b px-3 py-1 last:border-0"
						>
							<span className="text-muted-foreground">
								{line.field}:{" "}
							</span>
							{String(line.existing ?? "—")} →{" "}
							{String(line.incoming ?? "—")}
						</div>
					))}
				</div>
			) : (
				<p className="mt-4 rounded-md border bg-background p-3 text-xs text-muted-foreground">
					<FileCode2 className="mr-1 inline size-3.5" />
					No field-level diff is available for this item.
				</p>
			)}
			{conflict && (
				<div className="mt-4 grid grid-cols-2 gap-2">
					<Button
						type="button"
						variant={decision === "keep" ? "default" : "outline"}
						size="sm"
						onClick={() => onDecision("keep")}
					>
						Keep existing
					</Button>
					<Button
						type="button"
						variant={decision === "replace" ? "default" : "outline"}
						size="sm"
						onClick={() => onDecision("replace")}
					>
						Replace definition
					</Button>
				</div>
			)}
		</aside>
	);
}
