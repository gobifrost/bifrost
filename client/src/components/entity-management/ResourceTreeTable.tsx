import { useEffect, useMemo, useState } from "react";
import { Building2, ChevronRight, Globe, Shield, Trash2 } from "lucide-react";

import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import {
	useDependencyGraph,
	type EntityType as DependencyEntityType,
	type DependencyGraph,
} from "@/hooks/useDependencyGraph";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import { cn } from "@/lib/utils";

import type { EntityType, EntityWithScope, Organization, Role } from "./types";
import { ENTITY_CONFIG, formatEntityAccess, isEntityManaged } from "./types";

interface ResourceTreeTableProps {
	entities: EntityWithScope[];
	allEntities: EntityWithScope[];
	organizations: Organization[];
	roles: Role[];
	selectedIds: Set<string>;
	allSelected: boolean;
	someSelected: boolean;
	onSelectAll: (selected: boolean) => void;
	onSelect: (entityKey: string, selected: boolean) => void;
	onVisibleKeysChange?: (entityKeys: string[]) => void;
	onConnectedKeysChange?: (entityKeys: string[]) => void;
	onConnectedGraphChange?: (graph: {
		keys: string[];
		edges: { source: string; target: string }[];
	}) => void;
	onDelete: (
		entityId: string,
		entityName: string,
		entityType: EntityType,
	) => void;
}

interface ResourceRow {
	entity: EntityWithScope;
	depth: number;
	caption?: string;
	isLoading?: boolean;
	isError?: boolean;
	onRetry?: () => void;
	expandable: boolean;
	expanded: boolean;
	branchLast: boolean;
	ancestorGuides?: number[];
}

export function ResourceTreeTable({
	entities,
	allEntities,
	organizations,
	roles,
	selectedIds,
	onSelect,
	onVisibleKeysChange,
	onConnectedKeysChange,
	onConnectedGraphChange,
	onDelete,
}: ResourceTreeTableProps) {
	const [expandedKey, setExpandedKey] = useState<string | null>(null);
	const getOrgName = useOrganizationName(organizations);
	const expandedEntity = allEntities.find(
		(entity) => entity.key === expandedKey,
	);
	const {
		data: graphData,
		isLoading,
		isError,
		isFetching,
		refetch,
	} = useDependencyGraph(
		expandedEntity?.entityType as DependencyEntityType | undefined,
		expandedEntity?.id,
		3,
	);
	const rows = useResourceRows({
		rootEntities: entities,
		allEntities,
		expandedKey,
		graphData: graphData ?? null,
		isLoading: isLoading || isFetching,
		isError,
		onRetry: () => void refetch(),
	});
	const visibleKeys = useMemo(
		() => Array.from(new Set(rows.map((row) => row.entity.key))),
		[rows],
	);
	const connectedGraph = useMemo(
		() => loadedConnectedGraph(expandedKey, graphData ?? null, allEntities),
		[expandedKey, graphData, allEntities],
	);
	const connectedKeys = connectedGraph.keys;
	const visibleKeySignature = visibleKeys.join("\u0000");
	const connectedKeySignature = connectedKeys.join("\u0000");
	const connectedEdgeSignature = connectedGraph.edges
		.map((edge) => `${edge.source}->${edge.target}`)
		.join("\u0000");

	useEffect(() => {
		onVisibleKeysChange?.(visibleKeys);
		// visibleKeySignature intentionally gates updates so callers receive
		// row visibility changes without a parent/child render loop.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [onVisibleKeysChange, visibleKeySignature]);
	useEffect(() => {
		onConnectedKeysChange?.(connectedKeys);
		// connectedKeySignature intentionally gates updates so callers receive
		// expanded graph selection changes without a parent/child render loop.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [connectedKeySignature, onConnectedKeysChange]);
	useEffect(() => {
		onConnectedGraphChange?.(connectedGraph);
		// connectedEdgeSignature intentionally gates updates to loaded graph
		// edges without a parent/child render loop.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [connectedKeySignature, connectedEdgeSignature, onConnectedGraphChange]);

	const toggleExpanded = (entityKey: string) => {
		setExpandedKey((current) => (current === entityKey ? null : entityKey));
	};

	return (
		<div className="min-w-0 [--tree-indent:1rem] sm:[--tree-indent:2rem]">
			<ul aria-label="Resources" className="min-w-0">
				{rows.map((row) => (
					<li
						key={`${row.entity.key}:${row.depth}:${row.caption ?? "root"}`}
						className={cn(
							"relative border-b border-border/70 transition-colors last:border-b-0 motion-reduce:transition-none",
							row.depth > 0 &&
								"before:absolute before:left-[var(--row-indent)] before:top-0 before:h-full before:w-px before:bg-border after:absolute after:left-[var(--row-indent)] after:top-7 after:h-px after:w-4 after:bg-border",
							row.depth > 0 && row.branchLast && "before:h-7",
							selectedIds.has(row.entity.key) && !isEntityManaged(row.entity)
								? "tree-row-selected before:opacity-0 after:opacity-0"
								: "hover:bg-muted/20",
						)}
						style={{
							"--row-indent": `calc(var(--tree-indent) * ${Math.min(row.depth, 3)})`,
							paddingLeft: "var(--row-indent)",
						} as React.CSSProperties}
					>
						{!selectedIds.has(row.entity.key) && row.ancestorGuides?.map((depth) => (
							<span key={depth} aria-hidden="true" className="pointer-events-none absolute inset-y-0 w-px bg-border" style={{ left: `calc(var(--tree-indent) * ${Math.min(depth, 3)})` }} />
						))}
						<ResourceDirectoryRow
							row={row}
							organizationName={getOrgName(
								row.entity.organizationId,
								"Unknown organization",
							)}
							accessName={accessName(row.entity, roles)}
							selected={selectedIds.has(row.entity.key)}
							onToggleExpanded={toggleExpanded}
							onSelect={onSelect}
							onDelete={onDelete}
						/>
					</li>
				))}
			</ul>
		</div>
	);
}

function ResourceDirectoryRow({
	row,
	organizationName,
	accessName,
	selected,
	onToggleExpanded,
	onSelect,
	onDelete,
}: {
	row: ResourceRow;
	organizationName: string;
	accessName: string;
	selected: boolean;
	onToggleExpanded: (entityKey: string) => void;
	onSelect: (entityKey: string, selected: boolean) => void;
	onDelete: ResourceTreeTableProps["onDelete"];
}) {
	const { entity, caption, expandable, expanded, isLoading, isError } = row;
	const managed = isEntityManaged(entity);
	const config = ENTITY_CONFIG[entity.entityType];
	const Icon = config.icon;

	return (
		<div
			className={cn(
				"relative flex min-w-0 items-start gap-2 px-3 py-3 text-sm transition-colors sm:gap-3",
			)}
		>
			<div className="flex size-7 shrink-0 items-center justify-center">
				<Checkbox
					aria-label={`Select ${entity.name}`}
					checked={selected && !managed}
					disabled={managed}
					onCheckedChange={(value) =>
						onSelect(entity.key, value === true)
					}
				/>
			</div>
			{row.depth === 0 ? <ExpandButton
				entityName={entity.name}
				expandable={expandable}
				expanded={expanded}
				loading={isLoading}
				onClick={() => onToggleExpanded(entity.key)}
			/> : null}
			<div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] border border-border bg-background text-muted-foreground">
				<Icon className="size-4" />
			</div>
			<div className="min-w-0 flex-1">
				<ResourceLabel
					entity={entity}
					caption={
						isError ? "Could not load related resources" : caption
					}
					onRetry={row.onRetry}
				/>
				<div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs leading-5 text-muted-foreground">
					<TypeBadge entity={entity} />
					<ScopeText
						entity={entity}
						organizationName={organizationName}
					/>
					<AccessText name={accessName} />
				</div>
			</div>
			<div className="pt-1">
				<RowActions
					entity={entity}
					managed={managed}
					onDelete={onDelete}
				/>
			</div>
		</div>
	);
}

function ExpandButton({
	entityName,
	expandable,
	expanded,
	loading,
	onClick,
}: {
	entityName: string;
	expandable: boolean;
	expanded: boolean;
	loading?: boolean;
	onClick: () => void;
}) {
	if (!expandable) {
		return <span aria-hidden="true" className="size-7 shrink-0" />;
	}

	return (
		<Button
			type="button"
			variant="ghost"
			size="icon-sm"
			className="size-7 shrink-0"
			aria-label={`${expanded ? "Collapse" : "Expand"} ${entityName}`}
			aria-expanded={expanded}
			onClick={onClick}
		>
			<ChevronRight
				aria-hidden="true"
				className={cn(
					"size-4 transition-transform",
					expanded && "rotate-90",
					loading && "animate-pulse",
				)}
			/>
		</Button>
	);
}

function ResourceLabel({
	entity,
	caption,
	onRetry,
}: {
	entity: EntityWithScope;
	caption?: string;
	onRetry?: () => void;
}) {
	return (
		<div className="min-w-0">
			<div className="flex min-w-0 flex-wrap items-center gap-2">
				<p className="min-w-0 font-medium leading-5 text-foreground [overflow-wrap:anywhere]">
					{entity.name}
				</p>
				{isEntityManaged(entity) ? (
					<Badge variant="outline" className="h-5 px-1.5">
						Solution managed
					</Badge>
				) : null}
			</div>
			<p className="flex flex-wrap items-center gap-2 text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">
				<span>{caption ?? resourceUsageText(entity)}</span>
				{onRetry ? (
					<Button
						type="button"
						variant="link"
						size="sm"
						className="h-auto p-0 text-xs"
						onClick={onRetry}
					>
						Retry
					</Button>
				) : null}
			</p>
		</div>
	);
}

function TypeBadge({ entity }: { entity: EntityWithScope }) {
	const config = ENTITY_CONFIG[entity.entityType];
	return (
		<Badge variant="outline" className={cn("h-5 px-1.5", config.color)}>
			{config.label}
		</Badge>
	);
}

function RowActions({
	entity,
	managed,
	onDelete,
}: {
	entity: EntityWithScope;
	managed: boolean;
	onDelete: ResourceTreeTableProps["onDelete"];
}) {
	const config = ENTITY_CONFIG[entity.entityType];
	return !managed ? (
		<RecordActionsMenu label={`More actions for ${entity.name}`}>
			<DropdownMenuItem
				variant="destructive"
				onSelect={() =>
					onDelete(entity.id, entity.name, entity.entityType)
				}
			>
				<Trash2 aria-hidden="true" className="size-4" />
				Delete {config.label.toLowerCase()}
			</DropdownMenuItem>
		</RecordActionsMenu>
	) : null;
}

function ScopeText({
	entity,
	organizationName,
}: {
	entity: EntityWithScope;
	organizationName: string;
}) {
	return (
		<span className="flex min-w-0 items-center gap-2 text-foreground">
			{entity.organizationId ? (
				<Building2 className="size-4 shrink-0 text-muted-foreground" />
			) : (
				<Globe className="size-4 shrink-0 text-muted-foreground" />
			)}
			<span className="min-w-0 [overflow-wrap:anywhere]">
				{organizationName}
			</span>
		</span>
	);
}

function AccessText({ name }: { name: string }) {
	return (
		<span className="flex min-w-0 items-center gap-2 text-foreground">
			<Shield className="size-4 shrink-0 text-muted-foreground" />
			<span className="min-w-0 [overflow-wrap:anywhere]">{name}</span>
		</span>
	);
}

function accessName(entity: EntityWithScope, roles: Role[]) {
	if (entity.accessLevel === "role_based" && entity.roleIds.length > 0) {
		return entity.roleIds
			.map(
				(roleId) =>
					roles.find((role) => role.id === roleId)?.name ?? roleId,
			)
			.join(", ");
	}
	return formatEntityAccess(entity.accessLevel);
}

function resourceUsageText(entity: EntityWithScope) {
	if (entity.entityType === "app") return "App resource";
	if (entity.usedByCount === null) return "Relationship count unavailable";
	if (entity.entityType === "workflow") {
		return entity.usedByCount === 0
			? "No forms or agents use this workflow"
			: `Used by ${entity.usedByCount} resource${entity.usedByCount === 1 ? "" : "s"}`;
	}
	return entity.usedByCount === 0
		? "No workflow dependencies"
		: `Uses ${entity.usedByCount} workflow${entity.usedByCount === 1 ? "" : "s"}`;
}

function useResourceRows({
	rootEntities,
	allEntities,
	expandedKey,
	graphData,
	isLoading,
	isError,
	onRetry,
}: {
	rootEntities: EntityWithScope[];
	allEntities: EntityWithScope[];
	expandedKey: string | null;
	graphData: DependencyGraph | null;
	isLoading: boolean;
	isError: boolean;
	onRetry?: () => void;
}): ResourceRow[] {
	return useMemo(() => {
		const rows: ResourceRow[] = [];
		const byKey = new Map(
			allEntities.map((entity) => [entity.key, entity]),
		);
		for (const entity of rootEntities) {
			const expanded = expandedKey === entity.key;
			const childRows =
				expanded && !isLoading && !isError && graphData
					? relatedRows(entity, graphData, byKey)
					: [];
			rows.push({
				entity,
				depth: 0,
				caption: expanded
					? isLoading
						? "Loading related resources..."
						: isError
							? "Could not load related resources"
							: graphData && childRows.length === 0
								? "No related resources"
								: undefined
					: undefined,
				expandable: hasExpandableRelationships(entity),
				expanded: expanded && hasExpandableRelationships(entity),
				branchLast: false,
				isLoading: expanded && isLoading,
				isError: expanded && isError,
				onRetry: expanded && isError ? onRetry : undefined,
			});
			if (
				!expanded ||
				!hasExpandableRelationships(entity) ||
				isLoading ||
				isError ||
				!graphData
			)
				continue;
			for (const child of childRows) {
				rows.push(child);
			}
		}
		return rows;
	}, [
		rootEntities,
		allEntities,
		expandedKey,
		graphData,
		isLoading,
		isError,
		onRetry,
	]);
}

function hasExpandableRelationships(entity: EntityWithScope) {
	return entity.hasRelationships;
}

function relatedRows(
	root: EntityWithScope,
	graphData: DependencyGraph,
	byKey: Map<string, EntityWithScope>,
) {
	const neighbors = new Map<string, { key: string; caption: string }[]>();
	for (const edge of graphData.edges ?? []) {
		const source = byKey.get(edge.source);
		const target = byKey.get(edge.target);
		if (!source || !target) continue;
		const outgoing = neighbors.get(edge.source) ?? [];
		outgoing.push({
			key: edge.target,
			caption: relationshipCaption(edge.relationship, source.name, "out"),
		});
		neighbors.set(edge.source, outgoing);
		const incoming = neighbors.get(edge.target) ?? [];
		incoming.push({
			key: edge.source,
			caption: relationshipCaption(edge.relationship, target.name, "in"),
		});
		neighbors.set(edge.target, incoming);
	}

	const rows: ResourceRow[] = [];
	const seen = new Set([root.key]);
	const visit = (key: string, depth: number, caption: string) => {
		if (seen.has(key)) return;
		const entity = byKey.get(key);
		if (!entity) return;
		seen.add(key);
		rows.push({
			entity,
			depth,
				caption,
				expandable: false,
				expanded: false,
				branchLast: false,
			});
		for (const child of neighbors.get(key) ?? []) {
			visit(child.key, depth + 1, child.caption);
		}
	};
	for (const child of neighbors.get(root.key) ?? []) {
		visit(child.key, 1, child.caption);
	}
	for (let index = 0; index < rows.length; index += 1) {
		const nextSiblingOrAncestor = rows.slice(index + 1).find((next) => next.depth <= rows[index].depth);
		rows[index].branchLast = !nextSiblingOrAncestor || nextSiblingOrAncestor.depth < rows[index].depth;
	}
	const ancestors: ResourceRow[] = [];
	for (const row of rows) {
		while (ancestors.length && ancestors[ancestors.length - 1].depth >= row.depth) ancestors.pop();
		row.ancestorGuides = ancestors.filter((ancestor) => !ancestor.branchLast).map((ancestor) => ancestor.depth);
		ancestors.push(row);
	}
	return rows;
}

function loadedConnectedGraph(
	expandedKey: string | null,
	graphData: DependencyGraph | null,
	allEntities: EntityWithScope[],
) {
	if (!expandedKey || !graphData) return { keys: [], edges: [] };
	const byKey = new Set(allEntities.map((entity) => entity.key));
	const keys = new Set<string>();
	const edges: { source: string; target: string }[] = [];
	if (byKey.has(expandedKey)) keys.add(expandedKey);
	for (const edge of graphData.edges ?? []) {
		if (!byKey.has(edge.source) || !byKey.has(edge.target)) continue;
		keys.add(edge.source);
		keys.add(edge.target);
		edges.push({ source: edge.source, target: edge.target });
	}
	return { keys: Array.from(keys), edges };
}

function relationshipCaption(
	relationship: string,
	connectedName: string,
	direction: "out" | "in",
) {
	if (relationship === "uses") {
		return direction === "out"
			? `Used by ${connectedName}`
			: `Uses ${connectedName}`;
	}
	if (relationship === "used_by") {
		return direction === "out"
			? `Uses ${connectedName}`
			: `Used by ${connectedName}`;
	}
	return `${relationship
		.split("_")
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ")} ${connectedName}`;
}
