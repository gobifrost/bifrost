import type { ReactNode } from "react";
import { Building2, Calendar, Check, Database, Globe } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { cn } from "@/lib/utils";
import type { TablePublic } from "@/services/tables";

interface TableRecordListProps {
	tables: TablePublic[];
	isPlatformAdmin: boolean;
	selectionMode: boolean;
	selectedIds: Set<string>;
	allVisibleSelected: boolean;
	busy?: boolean;
	onToggleAll: () => void;
	onToggle: (id: string) => void;
	onOpen: (table: TablePublic) => void;
	scopeName: (id: string | null | undefined) => string;
	formatDate: (date: string | null) => string;
	renderActions: (table: TablePublic) => ReactNode;
}

function TableScopeMeta({
	table,
	isPlatformAdmin,
	scopeName,
}: {
	table: TablePublic;
	isPlatformAdmin: boolean;
	scopeName: (id: string | null | undefined) => string;
}) {
	const Icon = table.organization_id ? Building2 : Globe;
	return (
		<span className="inline-flex min-w-0 max-w-full items-center gap-1 text-xs leading-5 text-muted-foreground">
			<Icon aria-hidden="true" className="size-3.5 shrink-0" />
			<span className="min-w-0 [overflow-wrap:anywhere]">
				{table.organization_id
					? isPlatformAdmin
						? scopeName(table.organization_id)
						: "Organization"
					: "Global"}
			</span>
		</span>
	);
}

export function TableRecordList({
	tables,
	isPlatformAdmin,
	selectionMode,
	selectedIds,
	allVisibleSelected,
	busy = false,
	onToggleAll,
	onToggle,
	onOpen,
	scopeName,
	formatDate,
	renderActions,
}: TableRecordListProps) {
	return (
		<section
			className="flex min-h-0 flex-1 flex-col"
			aria-label="Data tables"
		>
			{isPlatformAdmin && selectionMode && (
				<div className="flex min-h-11 shrink-0 items-center justify-between gap-3 border-b border-border/70 bg-muted/20 px-3 py-2">
					<span className="text-sm text-muted-foreground">
						{selectedIds.size} selected
					</span>
					<Button
						type="button"
						variant="ghost"
						className="h-9"
						disabled={busy || tables.length === 0}
						onClick={onToggleAll}
					>
						{allVisibleSelected ? "Clear all" : "Select all"}
					</Button>
				</div>
			)}
			<ul aria-label="Data tables" className="min-h-0 overflow-y-auto">
				{tables.map((table) => {
					const isChecked = selectedIds.has(table.id);
					const titleId = `table-${table.id}-title`;
					const OpenTarget = selectionMode || busy ? "button" : "a";
					return (
						<li
							key={table.id}
							aria-label={table.name}
							className={cn(
								"group flex min-h-16 items-stretch border-b border-border/70 transition-colors last:border-b-0 motion-reduce:transition-none",
								selectionMode && isChecked
									? "tree-row-selected"
									: "hover:bg-muted/20",
							)}
						>
							<article
								aria-labelledby={titleId}
								className="flex min-h-16 min-w-0 flex-1"
							>
								<OpenTarget
									href={
										!selectionMode && !busy
											? `/tables/${table.id}`
											: undefined
									}
									aria-labelledby={titleId}
									type={
										OpenTarget === "button"
											? "button"
											: undefined
									}
									className="flex min-h-16 min-w-0 flex-1 items-start gap-3 px-3 py-3 text-left focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
									disabled={busy}
									aria-pressed={
										selectionMode ? isChecked : undefined
									}
									onClick={(event) => {
										if (selectionMode) {
											onToggle(table.id);
											return;
										}
										if (
											event.button !== 0 ||
											event.ctrlKey ||
											event.metaKey ||
											event.shiftKey ||
											event.altKey
										)
											return;
										event.preventDefault();
										onOpen(table);
									}}
								>
									<span className="flex size-9 shrink-0 items-center justify-center rounded-[var(--bf-radius-surface)] border border-primary/20 bg-primary/10 text-primary">
										{selectionMode && isChecked ? (
											<Check
												aria-hidden="true"
												className="size-4"
											/>
										) : (
											<Database
												aria-hidden="true"
												className="size-4"
											/>
										)}
									</span>
									<span className="@container min-w-0 flex-1 space-y-1">
										<span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
											<span
												id={titleId}
												className="block font-mono text-sm font-semibold leading-5 text-foreground [overflow-wrap:anywhere]"
											>
												{table.name}
											</span>
										</span>
										<span className="line-clamp-2 text-sm leading-5 text-muted-foreground [overflow-wrap:anywhere]">
											<MarkdownContent
												variant="preview"
												content={
													table.description ||
													"No description"
												}
											/>
										</span>
										<span className="grid min-w-0 grid-cols-1 @[260px]:grid-cols-[8rem_7rem] items-start gap-x-3 gap-y-1 pt-1 ">
											<TableScopeMeta
												table={table}
												isPlatformAdmin={
													isPlatformAdmin
												}
												scopeName={scopeName}
											/>
											<span className="inline-flex items-center gap-1 text-xs leading-5 text-muted-foreground">
												<Calendar
													aria-hidden="true"
													className="size-3.5 shrink-0"
												/>
												{formatDate(table.created_at)}
											</span>
										</span>
									</span>
								</OpenTarget>
							</article>
							<div className="flex shrink-0 items-center gap-1">
								{table.is_solution_managed && (
									<SolutionManagedBadge
										solutionId={table.solution_id}
									/>
								)}
								{renderActions(table)}
							</div>
						</li>
					);
				})}
			</ul>
		</section>
	);
}
