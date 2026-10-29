import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, RefreshCw, Shield, Trash2, Workflow } from "lucide-react";

import { BulkDeleteIdentitiesDialog } from "@/components/identities/BulkDeleteIdentitiesDialog";
import { IdentityActions } from "@/components/identities/IdentityActions";
import { IdentityKindBadge } from "@/components/identities/IdentityKindBadge";
import { IdentityOrganizationChip } from "@/components/identities/IdentityName";
import { NewIdentityDialog } from "@/components/identities/NewIdentityDialog";
import { ListLoadError } from "@/components/layout/ListLoadError";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { SearchBox } from "@/components/search/SearchBox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
	BulkActionBar,
	BulkActionButton,
} from "@/components/users/BulkActionBar";
import {
	BulkReplaceRolesDialog,
	BulkResultDialog,
	type BulkTarget,
} from "@/components/users/BulkUserDialogs";
import {
	ActionsCell,
	ActionsHead,
	RecordListBar,
	RecordSelectAll,
	RecordSelectTarget,
	RowSelectCheckbox,
	SelectionCell,
	SelectionHead,
} from "@/components/users/SelectableTable";
import { UsersViewTabs } from "@/components/users/UsersViewTabs";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useUserSelection } from "@/hooks/useUserSelection";
import { placeLabel } from "@/lib/role-boundaries";
import type { components } from "@/lib/v1";
import { cn } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";
import {
	identityLabel,
	identityOrganization,
	useIdentities,
	type Identity,
} from "@/services/identities";

type BulkUserResponse = components["schemas"]["BulkUserResponse"];

/** The global identity's row: a reach-tinted surface with a leading stripe. */
const PINNED_CLASS_NAME =
	"bg-[color-mix(in_srgb,var(--bf-reach-soft)_55%,transparent)] shadow-[inset_3px_0_0_var(--bf-reach)]";
/** The pinned row's sticky actions cell: the same tint, opaque over the card. */
const PINNED_ACTIONS_CLASS_NAME =
	"bg-[color-mix(in_srgb,var(--bf-reach-soft)_55%,var(--card))]";

/** The base role, then each additional role, with where it applies. */
function RoleChips({ identity }: { identity: Identity }) {
	return (
		<ul className="flex min-w-0 flex-wrap gap-1.5">
			<li>
				<Badge
					variant="power"
					title="Base role"
					className="h-auto min-h-6 whitespace-normal"
				>
					{identity.base_role.name}
				</Badge>
			</li>
			{identity.additional_roles.map((role) => (
				<li key={role.role_id}>
					<Badge
						variant="power"
						title={`Applies in ${role.boundaries
							.map((boundary) =>
								placeLabel(
									boundary.kind,
									boundary.organization_name ?? "",
								),
							)
							.join(", ")}`}
						className="h-auto min-h-6 whitespace-normal"
					>
						{role.name}
					</Badge>
				</li>
			))}
		</ul>
	);
}

function matches(identity: Identity, search: string): boolean {
	const term = search.trim().toLowerCase();
	return (
		!term ||
		identity.name.toLowerCase().includes(term) ||
		identityOrganization(identity).toLowerCase().includes(term)
	);
}

/**
 * The accounts that run work no person started: each organization's default
 * identity, the global one, and custom ones. Each opens on the person page.
 * The table is the Users list's: row selection with bulk actions (Replace
 * Roles; Delete, custom identities only) and a ⋮ menu on each row.
 */
export function Identities() {
	const navigate = useNavigate();
	const isNarrow = useMediaQuery("(max-width: 1023px)");
	const authorization = useAuthorization();
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [search, setSearch] = useState("");
	const identitiesQuery = useIdentities();
	const identities = useMemo(
		() =>
			(identitiesQuery.data ?? []).filter((identity) =>
				matches(identity, search),
			),
		[identitiesQuery.data, search],
	);
	const open = (identity: Pick<Identity, "id">) =>
		navigate(`/users/${identity.id}`);
	const pinned = (identity: Identity) =>
		identity.identity_kind === "global_default";

	// Bulk operations are offered where the caller holds them anywhere; the
	// server still decides each identity and reports the ones it refuses.
	const canReplaceRoles =
		authorization.canAnywhere("roleassignments.readwrite") &&
		// The replace-roles dialog lists every role, which needs roles.read.
		authorization.meets({ permission: "roles.read", at: "global" });
	const canDelete = authorization.canAnywhere("userlifecycle.readwrite");
	const showSelection = canReplaceRoles || canDelete;
	const selection = useUserSelection(identities);
	const deletable = selection.selectedItems.filter(
		(identity) => identity.identity_kind === "custom",
	);
	const [bulkMode, setBulkMode] = useState<"replace_roles" | "delete" | null>(
		null,
	);
	const [bulkResult, setBulkResult] = useState<BulkUserResponse | null>(null);
	const [bulkResultTargets, setBulkResultTargets] = useState<BulkTarget[]>(
		[],
	);
	const handlePartialFailure = (
		result: BulkUserResponse,
		targets: BulkTarget[],
	) => {
		setBulkResult(result);
		setBulkResultTargets(targets);
	};
	const closeBulk = () => setBulkMode(null);

	const selectLabel = (identity: Identity) =>
		`Select ${identityLabel(identity)}`;
	const renderActions = (identity: Identity) => (
		<IdentityActions
			identity={identity}
			label={`${identityLabel(identity)} actions`}
			onOpen={() => open(identity)}
		/>
	);

	return (
		<PageWorkspace className="mx-auto max-w-7xl">
			<ListPageHeader
				title="Identities"
				titleSlot={<UsersViewTabs />}
				description="The accounts workflows run as when no person starts them: each organization's default, the global default, and custom identities."
				actions={
					<>
						<Button
							variant="outline"
							size="icon"
							onClick={() => identitiesQuery.refetch()}
							title="Refresh"
							aria-label="Refresh identities"
							className="h-11 w-11 lg:h-9 lg:w-9"
							disabled={identitiesQuery.isFetching}
						>
							<RefreshCw
								className={cn(
									"h-4 w-4",
									identitiesQuery.isFetching &&
										"animate-spin motion-reduce:animate-none",
								)}
							/>
						</Button>
						{authorization.canAnywhere(
							"userlifecycle.readwrite",
						) && (
							<Button
								className="min-h-11 lg:min-h-0"
								onClick={() => setIsCreateOpen(true)}
							>
								<Plus className="mr-1.5 h-4 w-4" />
								New Identity
							</Button>
						)}
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={search}
					onChange={setSearch}
					placeholder="Search identities by name or organization..."
					className="w-full sm:flex-1"
				/>
			</ListToolbar>

			{identitiesQuery.isError && (
				<ListLoadError
					resource="identities"
					hasCachedData={!!identitiesQuery.data}
					isRetrying={identitiesQuery.isFetching}
					onRetry={() => void identitiesQuery.refetch()}
				/>
			)}
			<PageScrollArea
				aria-label="Identities list"
				className="lg:flex lg:flex-col lg:overflow-hidden"
				aria-busy={identitiesQuery.isFetching}
			>
				{identitiesQuery.isLoading ? (
					<div
						className="space-y-2"
						role="status"
						aria-label="Loading identities"
					>
						{[...Array(4)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : identitiesQuery.isError &&
				  !identitiesQuery.data ? null : identities.length === 0 ? (
					<EmptyState
						icon={Workflow}
						title={
							search ? "No Matching Identities" : "No Identities"
						}
						description={
							search
								? "Try a different name or organization."
								: "None of the organizations you can reach has an identity yet."
						}
					/>
				) : isNarrow ? (
					<div className="overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card">
						{showSelection && (
							<RecordListBar>
								<RecordSelectAll
									selection={selection}
									label="Select all visible identities"
								>
									Select All
								</RecordSelectAll>
							</RecordListBar>
						)}
						<ul aria-label="Identities" className="divide-y">
							{identities.map((identity) => (
								<li
									key={identity.id}
									data-pinned={pinned(identity) || undefined}
									className={cn(
										"min-w-0 space-y-3 p-4",
										pinned(identity) && PINNED_CLASS_NAME,
									)}
								>
									<div className="flex items-start gap-2">
										{showSelection && (
											<RecordSelectTarget>
												<RowSelectCheckbox
													selection={selection}
													id={identity.id}
													label={selectLabel(
														identity,
													)}
												/>
											</RecordSelectTarget>
										)}
										<div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
											<Link
												to={`/users/${identity.id}`}
												className="inline-flex min-h-11 items-center font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
											>
												{identity.name}
											</Link>
											<IdentityKindBadge
												kind={identity.identity_kind}
											/>
										</div>
										{renderActions(identity)}
									</div>
									<dl className="grid grid-cols-2 gap-3 text-sm">
										<div>
											<dt className="text-xs text-muted-foreground">
												Organization
											</dt>
											<dd className="mt-1">
												<IdentityOrganizationChip
													organizationName={
														identity.organization_name
													}
												/>
											</dd>
										</div>
										<div>
											<dt className="text-xs text-muted-foreground">
												Workflows Using
											</dt>
											<dd className="mt-1 font-mono text-xs tabular-nums">
												{identity.workflows_using}
											</dd>
										</div>
										<div className="col-span-2">
											<dt className="text-xs text-muted-foreground">
												Roles
											</dt>
											<dd className="mt-1">
												<RoleChips
													identity={identity}
												/>
											</dd>
										</div>
									</dl>
								</li>
							))}
						</ul>
					</div>
				) : (
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								{showSelection && (
									<SelectionHead
										selection={selection}
										label="Select all visible identities"
									/>
								)}
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
								<DataTableHead className="min-w-48">
									Name
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Kind
								</DataTableHead>
								<DataTableHead>Roles</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right">
									Workflows Using
								</DataTableHead>
								<ActionsHead />
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{identities.map((identity) => (
								<DataTableRow
									key={identity.id}
									href={`/users/${identity.id}`}
									onClick={() => open(identity)}
									data-pinned={pinned(identity) || undefined}
									className={cn(
										"group/row",
										pinned(identity) && PINNED_CLASS_NAME,
									)}
								>
									{showSelection && (
										<SelectionCell
											selection={selection}
											id={identity.id}
											label={selectLabel(identity)}
										/>
									)}
									<DataTableCell className="w-0 whitespace-nowrap text-sm">
										<IdentityOrganizationChip
											organizationName={
												identity.organization_name
											}
										/>
									</DataTableCell>
									<DataTableCell className="min-w-48 font-medium [overflow-wrap:anywhere]">
										<Link
											to={`/users/${identity.id}`}
											className="hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
										>
											{identity.name}
										</Link>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<IdentityKindBadge
											kind={identity.identity_kind}
										/>
									</DataTableCell>
									<DataTableCell>
										<RoleChips identity={identity} />
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right font-mono text-sm tabular-nums">
										{identity.workflows_using}
									</DataTableCell>
									<ActionsCell
										className={cn(
											pinned(identity) &&
												PINNED_ACTIONS_CLASS_NAME,
										)}
									>
										{renderActions(identity)}
									</ActionsCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</PageScrollArea>

			<BulkActionBar
				count={selection.count}
				label="Bulk identity actions"
				onClear={selection.clear}
			>
				{canReplaceRoles && (
					<BulkActionButton
						icon={Shield}
						onClick={() => setBulkMode("replace_roles")}
					>
						Replace Roles
					</BulkActionButton>
				)}
				{canDelete && (
					<BulkActionButton
						icon={Trash2}
						onClick={() => setBulkMode("delete")}
						disabled={deletable.length === 0}
						title={
							deletable.length === 0
								? "Default identities can't be deleted"
								: undefined
						}
					>
						Delete
					</BulkActionButton>
				)}
			</BulkActionBar>

			<BulkReplaceRolesDialog
				open={bulkMode === "replace_roles"}
				subject="identities"
				onOpenChange={(o) => !o && closeBulk()}
				users={selection.selectedItems}
				onPartialFailure={handlePartialFailure}
				onSuccess={selection.clear}
			/>
			<BulkDeleteIdentitiesDialog
				open={bulkMode === "delete"}
				onOpenChange={(o) => !o && closeBulk()}
				deletable={deletable}
				skipped={selection.count - deletable.length}
				onPartialFailure={handlePartialFailure}
				onSuccess={selection.clear}
			/>
			<BulkResultDialog
				open={bulkResult !== null}
				onOpenChange={(o) => !o && setBulkResult(null)}
				result={bulkResult}
				users={bulkResultTargets}
			/>

			<NewIdentityDialog
				open={isCreateOpen}
				onOpenChange={setIsCreateOpen}
				onCreated={open}
			/>
		</PageWorkspace>
	);
}
