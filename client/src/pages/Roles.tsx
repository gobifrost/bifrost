import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
	Plus,
	RefreshCw,
	UserCog,
	Users,
	FileText,
	Bot,
	LayoutGrid,
	Workflow,
	BookOpen,
	Pencil,
	Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { useRoles, useDeleteRole } from "@/hooks/useRoles";
import { RoleDialog } from "@/components/roles/RoleDialog";

import type { components } from "@/lib/v1";
type Role = components["schemas"]["RolePublic"];

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

export function Roles() {
	const [selectedRole, setSelectedRole] = useState<Role | undefined>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteOpen, setIsDeleteOpen] = useState(false);
	const [roleToDelete, setRoleToDelete] = useState<Role | undefined>();
	const [searchTerm, setSearchTerm] = useState("");

	const { data: roles, isLoading, refetch } = useRoles();
	const deleteRole = useDeleteRole();

	const filteredRoles = useSearch(roles || [], searchTerm, [
		"name",
		"description",
	]);

	const sortedRoles = useMemo(
		() =>
			[...(filteredRoles || [])].sort((a, b) =>
				(a.name || "").localeCompare(b.name || ""),
			),
		[filteredRoles],
	);

	const handleAdd = () => {
		setSelectedRole(undefined);
		setIsDialogOpen(true);
	};

	const handleEdit = (role: Role) => {
		setSelectedRole(role);
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
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">Roles</h1>
					<p className="mt-2 text-muted-foreground">
						Roles grant access to users, forms, agents, apps, workflows, and
						knowledge namespaces. Click a chip to manage a role's consumers.
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

			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search roles by name or description..."
					className="flex-1"
				/>
			</div>

			<div className="flex-1 min-h-0 overflow-y-auto">
				{isLoading ? (
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
						{[...Array(6)].map((_, i) => (
							<Skeleton key={i} className="h-48 w-full" />
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
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
						{sortedRoles.map((role) => (
							<RoleCard
								key={role.id}
								role={role}
								onEdit={() => handleEdit(role)}
								onDelete={() => handleDelete(role)}
							/>
						))}
					</div>
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

function RoleCard({
	role,
	onEdit,
	onDelete,
}: {
	role: Role;
	onEdit: () => void;
	onDelete: () => void;
}) {
	const counts = role.consumer_counts;

	return (
		<Card className="group transition-colors hover:border-primary/40">
			<CardContent className="p-5 flex flex-col gap-4">
				<div className="flex items-start justify-between gap-2">
					<Link
						to={`/roles/${role.id}`}
						className="flex-1 min-w-0 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
					>
						<div className="text-lg font-semibold truncate">{role.name}</div>
						{role.description && (
							<div className="text-sm text-muted-foreground line-clamp-2">
								{role.description}
							</div>
						)}
					</Link>
					<div className="flex gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
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
				</div>

				<div className="flex flex-wrap gap-1.5">
					{CHIP_DEFS.map(({ key, label, icon: Icon }) => {
						const count = counts ? counts[key] : 0;
						return (
							<Link
								key={key}
								to={`/roles/${role.id}/${key}`}
								className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-muted hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
								title={`${count} ${label.toLowerCase()}`}
								aria-label={`${count} ${label.toLowerCase()} — open ${label.toLowerCase()} tab`}
							>
								<Icon className="h-3 w-3" />
								<span className="font-medium">{count}</span>
								<span className="text-muted-foreground">{label}</span>
							</Link>
						);
					})}
				</div>
			</CardContent>
		</Card>
	);
}
