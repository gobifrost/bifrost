import { RoleActionsMenu } from "./roles/RoleActionsMenu";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
	ArrowDown,
	ArrowUp,
	BookOpen,
	Bot,
	FileText,
	LayoutGrid,
	Plus,
	RefreshCw,
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
	DataTableFooter,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { RoleDeleteDialog } from "@/components/roles/RoleDeleteDialog";
import { ListLoadError } from "@/components/layout/ListLoadError";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { SearchBox } from "@/components/search/SearchBox";
import { useDeleteRole, useRolesPage } from "@/hooks/useRoles";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { RoleDialog } from "@/components/roles/RoleDialog";
import { ListPagination } from "@/components/pagination/ListPagination";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";

import type { components } from "@/lib/v1";
type Role = components["schemas"]["RolePublic"];

type SortColumn = "name" | "created";
type SortDirection = "asc" | "desc";
const PAGE_SIZE = 25;

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

const EMPTY_CONSUMER_COUNTS: Record<(typeof CHIP_DEFS)[number]["key"], number> =
	{
		users: 0,
		forms: 0,
		agents: 0,
		apps: 0,
		workflows: 0,
		knowledge: 0,
	};

function getSortDirection(
	column: SortColumn,
	sortColumn: SortColumn,
	sortDirection: SortDirection,
) {
	if (sortColumn !== column) return undefined;
	return sortDirection === "asc" ? "ascending" : "descending";
}

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
		<ArrowUp className="size-3" />
	) : (
		<ArrowDown className="size-3" />
	);
}

function SortHeaderButton({
	column,
	label,
	sortColumn,
	sortDirection,
	onSort,
	className,
}: {
	column: SortColumn;
	label: string;
	sortColumn: SortColumn;
	sortDirection: SortDirection;
	onSort: (column: SortColumn) => void;
	className?: string;
}) {
	return (
		<DataTableHead
			aria-sort={getSortDirection(column, sortColumn, sortDirection)}
			className={className}
		>
			<button
				type="button"
				onClick={() => onSort(column)}
				className="inline-flex items-center gap-1.5 rounded-sm text-left font-medium outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
			>
				<span>{label}</span>
				<SortIcon
					column={column}
					sortColumn={sortColumn}
					sortDirection={sortDirection}
				/>
			</button>
		</DataTableHead>
	);
}

function MobileSortBar({
	sortColumn,
	sortDirection,
	onSort,
}: {
	sortColumn: SortColumn;
	sortDirection: SortDirection;
	onSort: (column: SortColumn) => void;
}) {
	return (
		<div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-2 lg:hidden">
			<Button
				type="button"
				variant={sortColumn === "name" ? "default" : "outline"}
				className="h-11 justify-between"
				onClick={() => onSort("name")}
				aria-pressed={sortColumn === "name"}
			>
				<span>Name</span>
				<SortIcon
					column="name"
					sortColumn={sortColumn}
					sortDirection={sortDirection}
				/>
			</Button>
			<Button
				type="button"
				variant={sortColumn === "created" ? "default" : "outline"}
				className="h-11 justify-between"
				onClick={() => onSort("created")}
				aria-pressed={sortColumn === "created"}
			>
				<span>Created</span>
				<SortIcon
					column="created"
					sortColumn={sortColumn}
					sortDirection={sortDirection}
				/>
			</Button>
			<Button
				type="button"
				variant="outline"
				className="h-11 w-11 shrink-0"
				onClick={() => onSort(sortColumn)}
				aria-label={`Sort ${sortDirection === "asc" ? "descending" : "ascending"}`}
			>
				{sortDirection === "asc" ? (
					<ArrowUp className="size-4" />
				) : (
					<ArrowDown className="size-4" />
				)}
			</Button>
		</div>
	);
}

function RoleCountLink({
	roleId,
	count,
	label,
	icon: Icon,
	mobile = false,
}: {
	roleId: string;
	count: number;
	label: string;
	icon: React.ComponentType<{ className?: string }>;
	mobile?: boolean;
}) {
	if (mobile) {
		return (
			<Link
				to={`/roles/${roleId}/${label.toLowerCase()}`}
				className="inline-flex h-11 min-w-0 items-center justify-between gap-2 rounded-[var(--bf-radius-control)] border border-border bg-muted px-3 text-sm text-foreground transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				aria-label={`${count} ${label.toLowerCase()} — open ${label.toLowerCase()} tab`}
			>
				<span className="inline-flex min-w-0 items-center gap-2">
					<Icon className="hidden size-4 shrink-0 sm:block" />
					<span className="whitespace-normal leading-5">{label}</span>
				</span>
				<span className="font-medium tabular-nums">{count}</span>
			</Link>
		);
	}

	return (
		<Tooltip key={label}>
			<TooltipTrigger asChild>
				<Link
					to={`/roles/${roleId}/${label.toLowerCase()}`}
					className="inline-flex items-center gap-1 rounded-[var(--bf-radius-control)] bg-muted px-2 py-0.5 text-xs transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					aria-label={`${count} ${label.toLowerCase()} — open ${label.toLowerCase()} tab`}
				>
					<Icon className="h-3 w-3" />
					<span className="font-medium">{count}</span>
				</Link>
			</TooltipTrigger>
			<TooltipContent>{label}</TooltipContent>
		</Tooltip>
	);
}

function RoleMobileRecord({
	role,
	onEdit,
	onDelete,
}: {
	role: Role;
	onEdit: () => void;
	onDelete: () => void;
}) {
	const counts = role.consumer_counts ?? EMPTY_CONSUMER_COUNTS;

	return (
		<li className="rounded-[var(--bf-radius-surface)] border border-border bg-card p-4">
			<article className="space-y-4">
				<div className="space-y-3">
					<div className="min-w-0 space-y-1.5">
						<Link
							to={`/roles/${role.id}`}
							className="block min-h-11 text-base font-semibold leading-6 [overflow-wrap:anywhere] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							{role.name}
						</Link>
						<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
							{role.description || "No description"}
						</p>
					</div>
				</div>

				<div className="space-y-2">
					<p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
						Consumers
					</p>
					<div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
						{CHIP_DEFS.map(({ key, label, icon }) => {
							const count = counts[key] ?? 0;
							return (
								<RoleCountLink
									key={key}
									roleId={role.id}
									count={count}
									label={label}
									icon={icon}
									mobile
								/>
							);
						})}
					</div>
				</div>

				<dl className="grid gap-1 text-sm">
					<div className="flex items-center justify-between gap-3">
						<dt className="text-muted-foreground">Created</dt>
						<dd className="font-medium">
							{role.created_at
								? new Date(role.created_at).toLocaleDateString()
								: "N/A"}
						</dd>
					</div>
				</dl>
				<RoleActionsMenu
					name={role.name}
					onEdit={onEdit}
					onDelete={onDelete}
				/>
			</article>
		</li>
	);
}

function RoleMobileList({
	roles,
	total,
	offset,
	isFetching,
	sortColumn,
	sortDirection,
	onSort,
	onPageChange,
	onEdit,
	onDelete,
}: {
	roles: Role[];
	total: number;
	offset: number;
	isFetching: boolean;
	sortColumn: SortColumn;
	sortDirection: SortDirection;
	onSort: (column: SortColumn) => void;
	onPageChange: (offset: number) => void;
	onEdit: (role: Role) => void;
	onDelete: (role: Role) => void;
}) {
	return (
		<div className="space-y-3 lg:hidden">
			<MobileSortBar
				sortColumn={sortColumn}
				sortDirection={sortDirection}
				onSort={onSort}
			/>
			<ul className="space-y-3">
				{roles.map((role) => (
					<RoleMobileRecord
						key={role.id}
						role={role}
						onEdit={() => onEdit(role)}
						onDelete={() => onDelete(role)}
					/>
				))}
			</ul>
			<ListPagination
				offset={offset}
				limit={PAGE_SIZE}
				total={total}
				isFetching={isFetching}
				onPageChange={onPageChange}
			/>
		</div>
	);
}

export function Roles() {
	const compactLayout = useMediaQuery("(max-width: 1023px)");
	const [selectedRole, setSelectedRole] = useState<Role | undefined>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteOpen, setIsDeleteOpen] = useState(false);
	const [roleToDelete, setRoleToDelete] = useState<Role | undefined>();
	const [searchTerm, setSearchTerm] = useState("");
	const [sortColumn, setSortColumn] = useState<SortColumn>("name");
	const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
	const [offset, setOffset] = useState(0);

	const navigate = useNavigate();
	const rolesQuery = useRolesPage({
		search: searchTerm,
		sortBy: sortColumn,
		sortDirection,
		limit: PAGE_SIZE,
		offset,
	});
	const roles = rolesQuery.data?.items ?? [];
	const total = rolesQuery.data?.total ?? 0;
	const deleteRole = useDeleteRole();

	const handleSort = (column: SortColumn) => {
		if (sortColumn === column) {
			setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
		} else {
			setSortColumn(column);
			setSortDirection("asc");
		}
		setOffset(0);
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
		deleteRole.reset();
		setRoleToDelete(role);
		setIsDeleteOpen(true);
	};

	const handleConfirmDelete = () => {
		if (!roleToDelete || deleteRole.isPending) return;
		deleteRole.mutate(
			{ params: { path: { role_id: roleToDelete.id } } },
			{
				onSuccess: () => {
					setIsDeleteOpen(false);
					setRoleToDelete(undefined);
				},
			},
		);
	};

	return (
		<PageWorkspace className="mx-auto max-w-7xl">
			<ListPageHeader
				title="Roles"
				description="Control access to forms, agents, apps, workflows, and knowledge. Select a count to manage assignments."
				actions={
					<>
						<Button
							variant="outline"
							size="icon"
							onClick={() => rolesQuery.refetch()}
							title="Refresh"
							aria-label="Refresh roles"
							className="h-11 w-11 lg:h-9 lg:w-9"
						>
							<RefreshCw className="h-4 w-4" />
						</Button>
						<Button
							className="min-h-11 lg:min-h-0"
							onClick={handleAdd}
						>
							<Plus className="h-4 w-4 mr-1.5" />
							Create role
						</Button>
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={(value) => {
						setSearchTerm(value);
						setOffset(0);
					}}
					placeholder="Search roles by name or description..."
					className="w-full sm:flex-1"
				/>
			</ListToolbar>

			{rolesQuery.isError && (
				<ListLoadError
					resource="roles"
					hasCachedData={!!rolesQuery.data}
					isRetrying={rolesQuery.isFetching}
					onRetry={() => void rolesQuery.refetch()}
				/>
			)}

			{/* Content */}
			<PageScrollArea
				aria-label="Roles list"
				className="lg:flex lg:flex-col lg:overflow-hidden"
			>
				{rolesQuery.isLoading ? (
					<div className="space-y-2">
						{[...Array(5)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : rolesQuery.isError &&
				  !rolesQuery.data ? null : roles.length === 0 ? (
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
								onClick={handleAdd}
								className="mt-4"
							>
								<Plus className="h-4 w-4" />
								Create role
							</Button>
						</CardContent>
					</Card>
				) : compactLayout ? (
					<RoleMobileList
						roles={roles}
						total={total}
						offset={offset}
						isFetching={rolesQuery.isFetching}
						sortColumn={sortColumn}
						sortDirection={sortDirection}
						onSort={handleSort}
						onPageChange={setOffset}
						onEdit={handleEdit}
						onDelete={handleDelete}
					/>
				) : (
					<DataTable
						className="max-h-full"
						aria-busy={rolesQuery.isFetching}
					>
						<DataTableHeader>
							<DataTableRow>
								<SortHeaderButton
									column="name"
									label="Name"
									sortColumn={sortColumn}
									sortDirection={sortDirection}
									onSort={handleSort}
									className="w-0 whitespace-nowrap"
								/>
								<DataTableHead>Description</DataTableHead>
								<DataTableHead className="whitespace-nowrap">
									Consumers
								</DataTableHead>
								<SortHeaderButton
									column="created"
									label="Created"
									sortColumn={sortColumn}
									sortDirection={sortDirection}
									onSort={handleSort}
									className="w-0 whitespace-nowrap"
								/>
								<DataTableHead className="sticky right-0 w-px whitespace-nowrap bg-muted text-right">
									Actions
								</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{roles.map((role) => (
								<RoleRow
									key={role.id}
									role={role}
									onEdit={() => handleEdit(role)}
									onDelete={() => handleDelete(role)}
									onNavigate={(to) => navigate(to)}
								/>
							))}
						</DataTableBody>
						<DataTableFooter>
							<DataTableRow>
								<DataTableCell colSpan={5} className="p-0">
									<ListPagination
										offset={offset}
										limit={PAGE_SIZE}
										total={total}
										isFetching={rolesQuery.isFetching}
										onPageChange={setOffset}
									/>
								</DataTableCell>
							</DataTableRow>
						</DataTableFooter>
					</DataTable>
				)}
			</PageScrollArea>

			<RoleDialog
				role={selectedRole}
				open={isDialogOpen}
				onClose={() => {
					setIsDialogOpen(false);
					setSelectedRole(undefined);
				}}
			/>

			<RoleDeleteDialog
				name={roleToDelete?.name ?? ""}
				open={isDeleteOpen}
				pending={deleteRole.isPending}
				error={deleteRole.isError}
				onOpenChange={setIsDeleteOpen}
				onDelete={handleConfirmDelete}
			/>
		</PageWorkspace>
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
			href={`/roles/${role.id}`}
			onClick={() => onNavigate(`/roles/${role.id}`)}
			className="group/row"
		>
			<DataTableCell className="min-w-0 w-0 whitespace-nowrap font-medium">
				<Link
					to={`/roles/${role.id}`}
					className="block min-w-0 truncate hover:underline"
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
							<RoleCountLink
								key={key}
								roleId={role.id}
								count={count}
								label={label}
								icon={Icon}
							/>
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
				className="sticky right-0 w-px whitespace-nowrap bg-card text-right group-hover/row:bg-[color-mix(in_oklch,var(--card),var(--muted)_50%)]"
				onClick={(e) => e.stopPropagation()}
			>
				<RoleActionsMenu
					name={role.name}
					onEdit={onEdit}
					onDelete={onDelete}
				/>
			</DataTableCell>
		</DataTableRow>
	);
}
