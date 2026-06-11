import { useState, useEffect, useRef } from "react";
import {
	Globe,
	Building2,
	Shield,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
	dropTargetForElements,
} from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { cn } from "@/lib/utils";
import type { Organization, Role } from "./types";

// Organization Drop Target Component
export interface OrgDropTargetProps {
	organization: Organization | null;
	onDrop: (entityIds: string[], orgId: string | null) => void;
}

export function OrgDropTarget({ organization, onDrop }: OrgDropTargetProps) {
	const ref = useRef<HTMLDivElement>(null);
	const [isDraggedOver, setIsDraggedOver] = useState(false);
	const [dragCount, setDragCount] = useState(0);

	const isGlobal = organization === null;
	const name = isGlobal ? "Global" : organization.name;

	useEffect(() => {
		const el = ref.current;
		if (!el) return;

		return dropTargetForElements({
			element: el,
			getData: () => ({
				type: "org-target",
				orgId: isGlobal ? null : organization.id,
			}),
			canDrop: ({ source }) => source.data["type"] === "entity",
			onDragEnter: ({ source }) => {
				setIsDraggedOver(true);
				setDragCount((source.data["entityCount"] as number) || 1);
			},
			onDragLeave: () => {
				setIsDraggedOver(false);
				setDragCount(0);
			},
			onDrop: ({ source }) => {
				setIsDraggedOver(false);
				setDragCount(0);
				const entityIds = source.data["entityIds"] as string[];
				onDrop(entityIds, isGlobal ? null : organization.id);
			},
		});
	}, [organization, isGlobal, onDrop]);

	return (
		<div
			ref={ref}
			className={cn(
				"flex items-center gap-2 px-4 py-4 rounded-lg border-2 border-dashed transition-all",
				isDraggedOver
					? "border-primary bg-primary/10"
					: "border-muted-foreground/25 hover:border-muted-foreground/50",
			)}
		>
			{isGlobal ? (
				<Globe className="h-4 w-4 text-muted-foreground" />
			) : (
				<Building2 className="h-4 w-4 text-muted-foreground" />
			)}
			<span className="text-sm font-medium">{name}</span>
			{isDraggedOver && dragCount > 1 && (
				<Badge variant="secondary" className="ml-auto">
					{dragCount}
				</Badge>
			)}
		</div>
	);
}

// Role Drop Target Component
export interface RoleDropTargetProps {
	role: Role | "authenticated" | "clear-roles";
	onDrop: (entityIds: string[], roleOrAccessLevel: string) => void;
}

export function RoleDropTarget({ role, onDrop }: RoleDropTargetProps) {
	const ref = useRef<HTMLDivElement>(null);
	const [isDraggedOver, setIsDraggedOver] = useState(false);
	const [dragCount, setDragCount] = useState(0);

	const isAuthenticated = role === "authenticated";
	const isClearRoles = role === "clear-roles";
	const name = isAuthenticated
		? "Everyone except external users"
		: isClearRoles
			? "Clear Roles"
			: role.name;
	const id = isAuthenticated
		? "authenticated"
		: isClearRoles
			? "clear-roles"
			: role.id;

	useEffect(() => {
		const el = ref.current;
		if (!el) return;

		return dropTargetForElements({
			element: el,
			getData: () => ({
				type: "role-target",
				roleId: id,
				isAccessLevel: isAuthenticated,
				isClearRoles: isClearRoles,
			}),
			canDrop: ({ source }) => source.data["type"] === "entity",
			onDragEnter: ({ source }) => {
				setIsDraggedOver(true);
				setDragCount((source.data["entityCount"] as number) || 1);
			},
			onDragLeave: () => {
				setIsDraggedOver(false);
				setDragCount(0);
			},
			onDrop: ({ source }) => {
				setIsDraggedOver(false);
				setDragCount(0);
				const entityIds = source.data["entityIds"] as string[];
				onDrop(entityIds, id);
			},
		});
	}, [id, isAuthenticated, isClearRoles, onDrop]);

	return (
		<div
			ref={ref}
			className={cn(
				"flex items-center gap-2 px-4 py-4 rounded-lg border-2 border-dashed transition-all",
				isDraggedOver
					? "border-primary bg-primary/10"
					: isClearRoles
						? "border-destructive/25 hover:border-destructive/50"
						: "border-muted-foreground/25 hover:border-muted-foreground/50",
			)}
		>
			<Shield className="h-4 w-4 text-muted-foreground" />
			<span className={cn("text-sm font-medium", isClearRoles && "text-destructive")}>
				{name}
			</span>
			{isDraggedOver && dragCount > 1 && (
				<Badge variant="secondary" className="ml-auto">
					{dragCount}
				</Badge>
			)}
		</div>
	);
}
