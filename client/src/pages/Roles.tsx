import { RoleActionsMenu } from "./roles/RoleActionsMenu";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, Plus, RefreshCw, UserCog } from "lucide-react";

import { GrantChip } from "@/components/access/GrantChip";
import { ReachChip } from "@/components/access/ReachChip";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
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
import { SearchBox } from "@/components/search/SearchBox";
import { useDeleteRole, useRolesPage } from "@/hooks/useRoles";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { RoleDialog } from "@/components/roles/RoleDialog";
import { getErrorMessage } from "@/lib/api-error";
import { permissionParts } from "@/lib/permission-words";
import { placementSummary } from "@/lib/role-boundaries";
import {
	usePermissionCatalog,
	type PermissionCatalogEntry,
} from "@/services/access";
import { useAuthorization } from "@/services/authorization";
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

/** Grant chips shown before "+N". */
const GRANTS_SHOWN = 4;

type Catalog = Map<string, PermissionCatalogEntry>;

/** Built-in roles first, then custom ones; empty sections are left out. */
function roleSections(roles: Role[]) {
	return [
		{
			id: "builtin",
			title: "Built-in",
			roles: roles.filter((role) => role.is_builtin),
		},
		{
			id: "custom",
			title: "Custom",
			roles: roles.filter((role) => !role.is_builtin),
		},
	].filter((section) => section.roles.length > 0);
}

/** The list carries grants, holders and placements only for Platform Admins. */
function hasSummaries(roles: Role[]) {
	return roles.some((role) => role.permissions != null);
}

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

/** What a role grants: the first few permissions, then a count of the rest. */
function RoleGrants({ role, catalog }: { role: Role; catalog: Catalog }) {
	const permissions = role.permissions ?? [];
	if (permissions.length === 0)
		return (
			<span className="text-sm text-muted-foreground">
				No permissions
			</span>
		);
	const hidden = permissions.length - GRANTS_SHOWN;
	return (
		<div className="flex flex-wrap items-center gap-1.5">
			<ul aria-label={`What ${role.name} grants`} className="contents">
				{permissions.slice(0, GRANTS_SHOWN).map((permission) => (
					<li key={permission}>
						<GrantChip
							permission={permission}
							entry={catalog.get(
								permissionParts(permission).domain,
							)}
						/>
					</li>
				))}
			</ul>
			{hidden > 0 && (
				<Link
					to={`/roles/${role.id}/permissions`}
					aria-label={`${hidden} more permissions`}
					className="inline-flex min-h-6 items-center rounded-[var(--bf-radius-control)] px-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					onClick={(e) => e.stopPropagation()}
				>
					+{hidden}
				</Link>
			)}
		</div>
	);
}

/** Where a role applies, across everyone who holds it. */
function RolePlaces({ role }: { role: Role }) {
	const places = role.placements
		? placementSummary(role.placements, role.is_base)
		: [];
	if (places.length === 0)
		return (
			<span className="text-sm text-muted-foreground">Not placed</span>
		);
	return (
		<ul
			aria-label={`Where ${role.name} applies`}
			className="flex flex-wrap gap-1.5"
		>
			{places.map((place) => (
				<li key={place.kind}>
					<ReachChip place={place} />
				</li>
			))}
		</ul>
	);
}

function SectionHeading({ id, children }: { id: string; children: ReactNode }) {
	return (
		<h2
			id={id}
			className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
		>
			{children}
		</h2>
	);
}

function RoleMobileRecord({
	role,
	catalog,
	summaries,
	canManage,
	onEdit,
	onDelete,
}: {
	role: Role;
	catalog: Catalog;
	summaries: boolean;
	canManage: boolean;
	onEdit: () => void;
	onDelete: () => void;
}) {
	return (
		<li className="rounded-[var(--bf-radius-surface)] border border-border bg-card p-4">
			<article className="space-y-4">
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

				{summaries && (
					<div className="space-y-2">
						<p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Grants
						</p>
						<RoleGrants role={role} catalog={catalog} />
					</div>
				)}

				<dl className="grid gap-2 text-sm">
					{summaries && (
						<>
							<div className="flex items-center justify-between gap-3">
								<dt className="text-muted-foreground">
									Holders
								</dt>
								<dd className="font-medium tabular-nums">
									{role.holders ?? 0}
								</dd>
							</div>
							<div className="flex items-start justify-between gap-3">
								<dt className="text-muted-foreground">
									Placed
								</dt>
								<dd className="flex min-w-0 justify-end">
									<RolePlaces role={role} />
								</dd>
							</div>
						</>
					)}
					<div className="flex items-center justify-between gap-3">
						<dt className="text-muted-foreground">Created</dt>
						<dd className="font-medium">
							{role.created_at
								? new Date(role.created_at).toLocaleDateString()
								: "N/A"}
						</dd>
					</div>
				</dl>
				{canManage && !role.is_builtin && (
					<RoleActionsMenu
						name={role.name}
						onEdit={onEdit}
						onDelete={onDelete}
					/>
				)}
			</article>
		</li>
	);
}

function RoleMobileList({
	roles,
	catalog,
	canManage,
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
	catalog: Catalog;
	canManage: boolean;
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
			{roleSections(roles).map((section) => (
				<section
					key={section.id}
					aria-labelledby={`roles-${section.id}`}
					className="space-y-2"
				>
					<SectionHeading id={`roles-${section.id}`}>
						{section.title}
					</SectionHeading>
					<ul className="space-y-3">
						{section.roles.map((role) => (
							<RoleMobileRecord
								key={role.id}
								role={role}
								catalog={catalog}
								summaries={hasSummaries(roles)}
								canManage={canManage}
								onEdit={() => onEdit(role)}
								onDelete={() => onDelete(role)}
							/>
						))}
					</ul>
				</section>
			))}
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
	const catalogQuery = usePermissionCatalog();
	const catalog = useMemo<Catalog>(
		() =>
			new Map(
				(catalogQuery.data ?? []).map((entry) => [entry.domain, entry]),
			),
		[catalogQuery.data],
	);
	const summaries = hasSummaries(roles);
	const columnCount = summaries ? 6 : 3;
	const deleteRole = useDeleteRole();
	const authorization = useAuthorization();
	const canManage = authorization.meets({
		permission: "roles.readwrite",
		at: "global",
	});

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
				description="What each role grants, who holds it, and where it applies. Built-in roles are read-only."
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
						{canManage && (
							<Button
								className="min-h-11 lg:min-h-0"
								onClick={handleAdd}
							>
								<Plus className="h-4 w-4 mr-1.5" />
								Create role
							</Button>
						)}
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
					<EmptyState
						icon={UserCog}
						title={
							searchTerm
								? "No roles match your search"
								: "No roles found"
						}
						description={
							searchTerm
								? "Try adjusting your search term or clear the filter"
								: "Get started by creating your first role"
						}
						action={
							canManage && (
								<Button variant="outline" onClick={handleAdd}>
									<Plus className="h-4 w-4" />
									Create role
								</Button>
							)
						}
					/>
				) : compactLayout ? (
					<RoleMobileList
						roles={roles}
						catalog={catalog}
						canManage={canManage}
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
									className="min-w-48"
								/>
								{summaries && (
									<>
										<DataTableHead>Grants</DataTableHead>
										<DataTableHead className="w-0 text-right">
											Holders
										</DataTableHead>
										<DataTableHead className="min-w-56">
											Placed
										</DataTableHead>
									</>
								)}
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
						{roleSections(roles).map((section) => (
							<DataTableBody
								key={section.id}
								aria-labelledby={`roles-${section.id}`}
							>
								<DataTableRow className="hover:bg-transparent">
									<th
										id={`roles-${section.id}`}
										colSpan={columnCount}
										scope="colgroup"
										className="bg-muted/40 px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
									>
										{section.title}
									</th>
								</DataTableRow>
								{section.roles.map((role) => (
									<RoleRow
										key={role.id}
										role={role}
										catalog={catalog}
										summaries={summaries}
										canManage={canManage}
										onEdit={() => handleEdit(role)}
										onDelete={() => handleDelete(role)}
										onNavigate={(to) => navigate(to)}
									/>
								))}
							</DataTableBody>
						))}
						<DataTableFooter>
							<DataTableRow>
								<DataTableCell
									colSpan={columnCount}
									className="p-0"
								>
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
				error={
					deleteRole.isError
						? getErrorMessage(
								deleteRole.error,
								"Could not delete the role. Try again.",
							)
						: null
				}
				onOpenChange={setIsDeleteOpen}
				onDelete={handleConfirmDelete}
			/>
		</PageWorkspace>
	);
}

function RoleRow({
	role,
	catalog,
	summaries,
	canManage,
	onEdit,
	onDelete,
	onNavigate,
}: {
	role: Role;
	catalog: Catalog;
	summaries: boolean;
	canManage: boolean;
	onEdit: () => void;
	onDelete: () => void;
	onNavigate: (to: string) => void;
}) {
	return (
		<DataTableRow
			clickable
			href={`/roles/${role.id}`}
			onClick={() => onNavigate(`/roles/${role.id}`)}
			className="group/row"
		>
			<DataTableCell className="min-w-48 max-w-xs">
				<Link
					to={`/roles/${role.id}`}
					className="block truncate font-medium hover:underline"
					onClick={(e) => e.stopPropagation()}
				>
					{role.name}
				</Link>
				{role.description && (
					<p className="truncate text-xs text-muted-foreground">
						{role.description}
					</p>
				)}
			</DataTableCell>
			{summaries && (
				<>
					<DataTableCell onClick={(e) => e.stopPropagation()}>
						<RoleGrants role={role} catalog={catalog} />
					</DataTableCell>
					<DataTableCell className="w-0 text-right font-medium tabular-nums">
						{role.holders ?? 0}
					</DataTableCell>
					<DataTableCell className="min-w-56">
						<RolePlaces role={role} />
					</DataTableCell>
				</>
			)}
			<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
				{role.created_at
					? new Date(role.created_at).toLocaleDateString()
					: "N/A"}
			</DataTableCell>
			<DataTableCell
				className="sticky right-0 w-px whitespace-nowrap bg-card text-right group-hover/row:bg-[color-mix(in_oklch,var(--card),var(--muted)_50%)]"
				onClick={(e) => e.stopPropagation()}
			>
				{canManage && !role.is_builtin && (
					<RoleActionsMenu
						name={role.name}
						onEdit={onEdit}
						onDelete={onDelete}
					/>
				)}
			</DataTableCell>
		</DataTableRow>
	);
}
