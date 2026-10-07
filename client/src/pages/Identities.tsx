import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, RefreshCw, Workflow } from "lucide-react";

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
import { UsersViewTabs } from "@/components/users/UsersViewTabs";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { placeLabel } from "@/lib/role-boundaries";
import { cn } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";
import {
	identityOrganization,
	useIdentities,
	type Identity,
} from "@/services/identities";

/** The global identity's row: a reach-tinted surface with a leading stripe. */
const PINNED_CLASS_NAME =
	"bg-[color-mix(in_srgb,var(--bf-reach-soft)_55%,transparent)] shadow-[inset_3px_0_0_var(--bf-reach)]";

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
 */
export function Identities() {
	const navigate = useNavigate();
	const isNarrow = useMediaQuery("(max-width: 1023px)");
	const authorization = useAuthorization();
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [search, setSearch] = useState("");
	const identitiesQuery = useIdentities();
	const identities = (identitiesQuery.data ?? []).filter((identity) =>
		matches(identity, search),
	);
	const open = (identity: Pick<Identity, "id">) =>
		navigate(`/users/${identity.id}`);
	const pinned = (identity: Identity) =>
		identity.identity_kind === "global_default";

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
							"users.lifecycle.readwrite",
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
					<ul
						aria-label="Identities"
						className="divide-y overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card"
					>
						{identities.map((identity) => (
							<li
								key={identity.id}
								data-pinned={pinned(identity) || undefined}
								className={cn(
									"min-w-0 space-y-3 p-4",
									pinned(identity) && PINNED_CLASS_NAME,
								)}
							>
								<div className="flex flex-wrap items-center gap-2">
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
											<RoleChips identity={identity} />
										</dd>
									</div>
								</dl>
							</li>
						))}
					</ul>
				) : (
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
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
										pinned(identity) && PINNED_CLASS_NAME,
									)}
								>
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
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</PageScrollArea>

			<NewIdentityDialog
				open={isCreateOpen}
				onOpenChange={setIsCreateOpen}
				onCreated={open}
			/>
		</PageWorkspace>
	);
}
