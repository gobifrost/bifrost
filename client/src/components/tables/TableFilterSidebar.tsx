/**
 * Table Filter Sidebar Component
 *
 * Collapsible sidebar for filtering table documents with query conditions.
 * Similar to WorkflowSidebar but tailored for document querying.
 */

import { useState } from "react";
import {
	ChevronDown,
	ChevronRight,
	Plus,
	Trash2,
	X,
	Filter,
	PanelLeftClose,
	Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { QueryOperator } from "@/services/tables";

interface FilterCondition {
	id: string;
	field: string;
	operator: QueryOperator;
	value: string;
}

const OPERATORS: { value: QueryOperator; label: string }[] = [
	{ value: "eq", label: "equals" },
	{ value: "ne", label: "not equals" },
	{ value: "contains", label: "contains" },
	{ value: "starts_with", label: "starts with" },
	{ value: "ends_with", label: "ends with" },
	{ value: "gt", label: ">" },
	{ value: "gte", label: ">=" },
	{ value: "lt", label: "<" },
	{ value: "lte", label: "<=" },
	{ value: "in", label: "in list" },
	{ value: "is_null", label: "is null" },
	{ value: "has_key", label: "has field" },
];

function generateId() {
	return Math.random().toString(36).substring(2, 9);
}

interface FiltersSectionProps {
	conditions: FilterCondition[];
	onAdd: () => void;
	onRemove: (id: string) => void;
	onUpdate: (id: string, updates: Partial<FilterCondition>) => void;
}

function FiltersSection({
	conditions,
	onAdd,
	onRemove,
	onUpdate,
}: FiltersSectionProps) {
	const [isExpanded, setIsExpanded] = useState(true);

	return (
		<div>
			<button
				onClick={() => setIsExpanded(!isExpanded)}
				className="flex w-full items-center justify-between py-3 pl-4 pr-4 text-left transition-colors hover:bg-muted/50"
			>
				<div className="flex items-center gap-2">
					{isExpanded ? (
						<ChevronDown className="size-4 text-muted-foreground" />
					) : (
						<ChevronRight className="size-4 text-muted-foreground" />
					)}
					<Filter className="size-4 text-muted-foreground" />
					<span className="font-medium text-sm">Query Filters</span>
				</div>
				<Badge variant="secondary" className="text-xs">
					{conditions.length}
				</Badge>
			</button>

			{isExpanded && (
				<div className="px-3 pb-3">
					{conditions.length === 0 ? (
						<div className="py-2 text-center text-xs italic text-muted-foreground">
							No filters applied
						</div>
					) : (
						<div className="space-y-2">
							{conditions.map((condition) => (
								<div
									key={condition.id}
									className="flex flex-col gap-1.5 rounded-xl bg-muted/50 p-2 ring-1 ring-foreground/5"
								>
									<div className="flex items-center gap-1">
										<Input
											placeholder="Field"
											value={condition.field}
											onChange={(e) =>
												onUpdate(condition.id, {
													field: e.target.value,
												})
											}
											className="flex-1"
										/>
										<Button
											variant="ghost"
											size="icon"
											onClick={() =>
												onRemove(condition.id)
											}
											className="shrink-0 text-muted-foreground"
										>
											<Trash2 className="size-3.5" />
										</Button>
									</div>
									<div className="flex items-center gap-1">
										<Select
											value={condition.operator}
											onValueChange={(
												value: QueryOperator,
											) =>
												onUpdate(condition.id, {
													operator: value,
												})
											}
										>
											<SelectTrigger className="w-[104px]">
												<SelectValue />
											</SelectTrigger>
											<SelectContent>
												{OPERATORS.map((op) => (
													<SelectItem
														key={op.value}
														value={op.value}
													>
														{op.label}
													</SelectItem>
												))}
											</SelectContent>
										</Select>
										{condition.operator === "is_null" ||
										condition.operator === "has_key" ? (
											<Select
												value={
													condition.value || "true"
												}
												onValueChange={(value) =>
													onUpdate(condition.id, {
														value,
													})
												}
											>
												<SelectTrigger className="flex-1">
													<SelectValue />
												</SelectTrigger>
												<SelectContent>
													<SelectItem value="true">
														true
													</SelectItem>
													<SelectItem value="false">
														false
													</SelectItem>
												</SelectContent>
											</Select>
										) : (
											<Input
												placeholder="Value"
												value={condition.value}
												onChange={(e) =>
													onUpdate(condition.id, {
														value: e.target.value,
													})
												}
												className="flex-1"
											/>
										)}
									</div>
								</div>
							))}
						</div>
					)}
					<Button
						variant="outline"
						size="sm"
						onClick={onAdd}
						className="mt-2 w-full"
					>
						<Plus />
						Add Filter
					</Button>
				</div>
			)}
		</div>
	);
}

export interface TableFilterSidebarProps {
	/** Callback to apply filters */
	onApplyFilters: (where: Record<string, unknown>) => void;
	/** Callback to clear all filters */
	onClearFilters: () => void;
	/** Whether there are active filters */
	hasActiveFilters: boolean;
	/** Callback to close/collapse the sidebar */
	onClose?: () => void;
	/** Additional CSS classes */
	className?: string;
}

/**
 * Table Filter Sidebar
 *
 * Provides a sidebar interface for building document query filters.
 */
export function TableFilterSidebar({
	onApplyFilters,
	onClearFilters,
	hasActiveFilters,
	onClose,
	className,
}: TableFilterSidebarProps) {
	const [conditions, setConditions] = useState<FilterCondition[]>([]);

	const addCondition = () => {
		setConditions((prev) => [
			...prev,
			{
				id: generateId(),
				field: "",
				operator: "eq",
				value: "",
			},
		]);
	};

	const removeCondition = (id: string) => {
		setConditions((prev) => prev.filter((c) => c.id !== id));
	};

	const updateCondition = (id: string, updates: Partial<FilterCondition>) => {
		setConditions((prev) =>
			prev.map((c) => (c.id === id ? { ...c, ...updates } : c)),
		);
	};

	const buildWhereClause = (): Record<string, unknown> => {
		const where: Record<string, unknown> = {};

		for (const condition of conditions) {
			if (!condition.field.trim()) continue;

			let value: unknown;

			switch (condition.operator) {
				case "is_null":
					value = { is_null: condition.value === "true" };
					break;
				case "has_key":
					value = { has_key: condition.value === "true" };
					break;
				case "in":
					value = {
						in: condition.value.split(",").map((v) => v.trim()),
					};
					break;
				case "eq":
					value = condition.value;
					break;
				default:
					value = { [condition.operator]: condition.value };
			}

			where[condition.field] = value;
		}

		return where;
	};

	const handleApply = () => {
		const where = buildWhereClause();
		onApplyFilters(where);
	};

	const handleClear = () => {
		setConditions([]);
		onClearFilters();
	};

	const hasConditions = conditions.length > 0;

	return (
		<div
			className={cn(
				"flex h-full flex-col overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10",
				className,
			)}
		>
			{/* Header */}
			<div className="flex items-center justify-between border-b px-4 py-3">
				<span className="font-medium text-sm">Filters</span>
				<div className="flex items-center gap-1">
					{hasActiveFilters && (
						<Button
							variant="ghost"
							size="xs"
							onClick={handleClear}
						>
							<X />
							Clear
						</Button>
					)}
					{onClose && (
						<Button
							variant="ghost"
							size="icon-xs"
							onClick={onClose}
							title="Hide filters"
						>
							<PanelLeftClose className="size-4" />
						</Button>
					)}
				</div>
			</div>

			{/* Active Filter Indicator */}
			{hasActiveFilters && (
				<div className="border-b bg-primary/5 px-4 py-2">
					<div className="text-xs text-muted-foreground">
						Active filters applied
					</div>
				</div>
			)}

			{/* Filter Sections */}
			<div className="flex-1 overflow-auto">
				<FiltersSection
					conditions={conditions}
					onAdd={addCondition}
					onRemove={removeCondition}
					onUpdate={updateCondition}
				/>
			</div>

			{/* Apply Button */}
			{hasConditions && (
				<div className="border-t p-3">
					<Button onClick={handleApply} className="w-full">
						<Search />
						Apply Filters
					</Button>
				</div>
			)}
		</div>
	);
}

export default TableFilterSidebar;
