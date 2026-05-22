import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
	ArrowDown,
	ArrowUp,
	BookOpen,
	Bot,
	FileText,
	LayoutGrid,
	Pencil,
	Plus,
	RefreshCw,
	Trash2,
	UserCog,
	Users,
	Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { useRoles, useDeleteRole } from "@/hooks/useRoles";
import { RoleDialog } from "@/components/roles/RoleDialog";

import type { components } from "@/lib/v1";
type Role = components["schemas"]["RolePublic"];

type SortColumn = "name" | "created";
type SortDirection = "asc" | "desc";

const CHIP_DEFS: {
	key: "users" | "forms" | "agents" | "apps" | "workflows" | "knowledge";
	label: string;
	icon: React.ComponentType<{ className?: string }>;
}[] = [
	{ key: "users", label: "Users", icon: Users },
	{ key: "forms", label: "Forms", icon: FileText },
	{ key: "agents", label: "Agents", icon: Bot },
	{ key: "apps", label: "Apps", icon: LayoutGrid },
	{ key: "workflows", label: "Workflows", icon: Workflow },
	{ key: "knowledge", label: "Knowledge", icon: BookOpen },
];

function SortIcon({
	column,
	sortColumn,
	sortDirection,
}: {
	column: SortColumn;
	sortColumn: SortColumn;
	sortDirection: SortDirection;
}) {
	if (sortColumn !== column) return null;
	return sortDirection === "asc" ? (
		<ArrowUp className="inline ml-1 h-3 w-3" />
	) : (
		<ArrowDown className="inline ml-1 h-3 w-3" />
	);
}

export function Roles() {
	const [selectedRole, setSelectedRole] = useState<Role | undefined>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteOpen, setIsDeleteOpen] = useState(false);
	const [roleToDelete, setRoleToDelete] = useState<Role | undefined>();
	const [searchTerm, setSearchTerm] = useState("");
	const [sortColumn, setSortColumn] = useState<SortColumn>("name");
	const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

	const navigate = useNavigate();
	const { data: roles, isLoading, refetch } = useRoles();
	const deleteRole = useDeleteRole();

	const filteredRoles = useSearch(roles || [], searchTerm, [
		"name",
		"description",
	]);

	const sortedRoles = useMemo(() => {
		if (!filteredRoles) return [];
		return [...filteredRoles].sort((a, b) => {
			const dir = sortDirection === "asc" ? 1 : -1;
			switch (sortColumn) {
				case "name":
					return dir * (a.name || "").localeCompare(b.name || "");
				case "created": {
					const aDate = a.created_at ? new Date(a.created_at).getTime() : 0;
					const bDate = b.created_at ? new Date(b.created_at).getTime() : 0;
					return dir * (aDate - bDate);
				}
				default:
					return 0;
			}
		});
	}, [filteredRoles, sortColumn, sortDirection]);

	const handleSort = (column: SortColumn) => {
		if (sortColumn === column) {
			setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
		} else {
			setSortColumn(column);
			setSortDirection("asc");
		}
	};

	const handleEdit = (role: Role) => {
		setSelectedRole(role);
		setIsDialogOpen(true);
	};

	const handleAdd = () => {
		setSelectedRole(undefined);
		setIsDialogOpen(true);
	};

	const handleDelete = (role: Role) => {
		setRoleToDelete(role);
		setIsDeleteOpen(true);
	};

	const handleConfirmDelete = () => {
		if (!roleToDelete) return;
		deleteRole.mutate({ params: { path: { role_id: roleToDelete.id } } });
		setIsDeleteOpen(false);
		setRoleToDelete(undefined);
	};

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">Roles</h1>
					<p className="mt-2 text-muted-foreground">
						Roles grant access to users, forms, agents, apps, workflows, and
						knowledge namespaces. Click a count chip to manage that consumer
						type.
					</p>
				</div>
				<div className="flex gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="icon"
						onClick={handleAdd}
						title="Create Role"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Filters */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search roles by name or description..."
					className="flex-1"
				/>
			</div>

			{/* Content */}
			<div className="flex-1 min-h-0">
				{isLoading ? (
					<div className="space-y-2">
						{[...Array(5)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : sortedRoles.length === 0 ? (
					<Card>
						<CardContent className="flex flex-col items-center justify-center py-12 text-center">
							<UserCog className="h-12 w-12 text-muted-foreground" />
							<h3 className="mt-4 text-lg font-semibold">
								{searchTerm
									? "No roles match your search"
									: "No roles found"}
							</h3>
							<p className="mt-2 text-sm text-muted-foreground">
								{searchTerm
									? "Try adjusting your search term or clear the filter"
									: "Get started by creating your first role"}
							</p>
							<Button
								variant="outline"
								size="icon"
								onClick={handleAdd}
								title="Create Role"
								className="mt-4"
							>
								<Plus className="h-4 w-4" />
							</Button>
						</CardContent>
					</Card>
				) : (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("name")}
								>
									Name
									<SortIcon
										column="name"
										sortColumn={sortColumn}
										sortDirection={sortDirection}
									/>
								</DataTableHead>
								<DataTableHead>Description</DataTableHead>
								<DataTableHead className="whitespace-nowrap">
									Consumers
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("created")}
								>
									Created
									<SortIcon
										column="created"
										sortColumn={sortColumn}
										sortDirection={sortDirection}
									/>
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right sticky right-0 bg-background">
									Actions
								</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{sortedRoles.map((role) => (
								<RoleRow
									key={role.id}
									role={role}
									onEdit={() => handleEdit(role)}
									onDelete={() => handleDelete(role)}
									onNavigate={(to) => navigate(to)}
								/>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</div>

			<RoleDialog
				role={selectedRole}
				open={isDialogOpen}
				onClose={() => {
					setIsDialogOpen(false);
					setSelectedRole(undefined);
				}}
			/>

			<AlertDialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Role</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete the role "{roleToDelete?.name}"?
							This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteRole.isPending ? "Deleting..." : "Delete Role"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}

function RoleRow({
	role,
	onEdit,
	onDelete,
	onNavigate,
}: {
	role: Role;
	onEdit: () => void;
	onDelete: () => void;
	onNavigate: (to: string) => void;
}) {
	const counts = role.consumer_counts;

	return (
		<DataTableRow
			clickable
			onClick={() => onNavigate(`/roles/${role.id}`)}
			className="group/row"
		>
			<DataTableCell className="w-0 whitespace-nowrap font-medium">
				<Link
					to={`/roles/${role.id}`}
					className="hover:underline"
					onClick={(e) => e.stopPropagation()}
				>
					{role.name}
				</Link>
			</DataTableCell>
			<DataTableCell className="max-w-xs truncate text-muted-foreground">
				{role.description || "-"}
			</DataTableCell>
			<DataTableCell
				className="whitespace-nowrap"
				onClick={(e) => e.stopPropagation()}
			>
				<div className="flex flex-wrap gap-1">
					{CHIP_DEFS.map(({ key, label, icon: Icon }) => {
						const count = counts ? counts[key] : 0;
						return (
							<Tooltip key={key}>
								<TooltipTrigger asChild>
									<Link
										to={`/roles/${role.id}/${key}`}
										className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-muted hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
										aria-label={`${count} ${label.toLowerCase()} — open ${label.toLowerCase()} tab`}
									>
										<Icon className="h-3 w-3" />
										<span className="font-medium">{count}</span>
									</Link>
								</TooltipTrigger>
								<TooltipContent>{label}</TooltipContent>
							</Tooltip>
						);
					})}
				</div>
			</DataTableCell>
			<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
				{role.created_at
					? new Date(role.created_at).toLocaleDateString()
					: "N/A"}
			</DataTableCell>
			<DataTableCell
				className="w-0 whitespace-nowrap text-right sticky right-0 bg-card group-hover/row:bg-[color-mix(in_oklch,var(--card),var(--muted)_50%)]"
				onClick={(e) => e.stopPropagation()}
			>
				<div className="flex justify-end gap-1">
					<Button
						variant="ghost"
						size="icon"
						className="h-8 w-8"
						onClick={onEdit}
						aria-label={`Edit ${role.name}`}
						title="Edit role"
					>
						<Pencil className="h-4 w-4" />
					</Button>
					<Button
						variant="ghost"
						size="icon"
						className="h-8 w-8"
						onClick={onDelete}
						aria-label={`Delete ${role.name}`}
						title="Delete role"
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			</DataTableCell>
		</DataTableRow>
	);
}
