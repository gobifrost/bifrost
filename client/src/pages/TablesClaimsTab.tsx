import { WorkspacePrimaryAction } from "@/components/layout/WorkspacePrimaryAction";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
	Building2,
	KeyRound,
	Loader2,
	Pencil,
	Plus,
	RefreshCw,
	Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { CustomClaimEditor } from "@/components/tables/CustomClaimEditor";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { ClaimDeleteDialog } from "./tables/ClaimDeleteDialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { SearchBox } from "@/components/search/SearchBox";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { useSearch } from "@/hooks/useSearch";
import { Label } from "@/components/ui/label";
import { orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import {
	createClaim,
	deleteClaim,
	listClaims,
	updateClaim,
	type CustomClaim,
} from "@/services/claims";

interface EditingState {
	claim: CustomClaim;
	originalName: string | null;
	scope?: string;
}

const EMPTY_CLAIM: CustomClaim = {
	id: "00000000-0000-4000-8000-000000000000",
	organization_id: "00000000-0000-4000-8000-000000000000",
	name: "",
	description: "",
	type: "list",
	query: { table: "", select: "" },
	is_solution_managed: false,
};

export function TablesClaimsTab() {
	const addClaimRef = useRef<HTMLButtonElement>(null);
	const compactLayout = useMediaQuery("(max-width: 1023px)");
	const { user } = useAuth();
	const authorization = useAuthorization();
	const canReadAnyClaims = authorization.canAnywhere("claims.read");
	const canManageAnyClaims = authorization.canAnywhere("claims.readwrite");
	const { data: organizations } = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	});

	const [searchTerm, setSearchTerm] = useState("");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [editing, setEditing] = useState<EditingState | null>(null);
	const [claimToDelete, setClaimToDelete] = useState<CustomClaim | null>(
		null,
	);

	const getOrgName = useOrganizationName(organizations);

	const apiScope =
		filterOrgId === undefined || filterOrgId === null
			? undefined
			: filterOrgId;

	const {
		data,
		error,
		isFetching: loading,
		isLoading: initialLoading,
		refetch: refresh,
	} = useQuery({
		queryKey: ["custom-claims", { scope: apiScope }],
		queryFn: ({ signal }) => listClaims({ signal, scope: apiScope }),
	});
	const claims = data?.claims ?? [];
	const hasLoaded = data !== undefined;
	const loadError = Boolean(error);
	const knownOrganizations = (() => {
		const known = new Map<string, { id: string; name: string }>();
		for (const claim of claims) {
			if (claim.organization_id) {
				known.set(claim.organization_id, {
					id: claim.organization_id,
					name: getOrgName(claim.organization_id),
				});
			}
		}
		if (user?.organizationId) {
			known.set(user.organizationId, {
				id: user.organizationId,
				name: getOrgName(user.organizationId),
			});
		}
		for (const grant of authorization.authorization?.grants ?? []) {
			if (
				grant.boundary.kind === "organization" &&
				grant.boundary.organization_id
			) {
				known.set(grant.boundary.organization_id, {
					id: grant.boundary.organization_id,
					name: getOrgName(grant.boundary.organization_id),
				});
			}
		}
		return [...known.values()];
	})();
	const getClaimScope = (claim: CustomClaim): string | undefined =>
		claim.organization_id ?? undefined;
	const canManageClaim = (claim: CustomClaim) =>
		!claim.is_solution_managed &&
		authorization.canAt("claims.readwrite", orgTarget(claim.organization_id));

	const filteredClaims = useSearch(claims, searchTerm, [
		"name",
		"description",
	]);

	async function handleSave(claim: CustomClaim) {
		const scope = editing?.scope;
		if (!scope || !authorization.canAt("claims.readwrite", orgTarget(scope))) {
			return;
		}
		if (editing?.originalName) {
			await updateClaim(
				editing.originalName,
				{
					description: claim.description,
					type: claim.type,
					query: claim.query,
				},
				{ scope },
			);
			toast.success("Claim updated");
		} else {
			await createClaim(
				{
					name: claim.name,
					description: claim.description,
					type: claim.type,
					query: claim.query,
				},
				{ scope },
			);
			toast.success("Claim created");
		}
		setEditing(null);
		await refresh();
	}

	async function handleDeleteConfirmed() {
		if (!claimToDelete) return;
		if (!canManageClaim(claimToDelete)) return;
		await deleteClaim(claimToDelete.name, {
			scope: getClaimScope(claimToDelete),
		});
		toast.success("Claim deleted");
		setClaimToDelete(null);
		await refresh();
	}

	function handleAdd() {
		const defaultOrg =
			user?.organizationId &&
			authorization.canAt("claims.readwrite", orgTarget(user.organizationId))
				? user.organizationId
				: undefined;
		setEditing({
			claim: { ...EMPTY_CLAIM, organization_id: defaultOrg ?? "" },
			originalName: null,
			scope: defaultOrg,
		});
	}

	const renderClaimActions = (claim: CustomClaim) => (
		<RecordActionsMenu label={`${claim.name} actions`}>
			<DropdownMenuItem
				className="min-h-11"
				onSelect={() =>
					setEditing({
						claim,
						originalName: claim.name,
						scope: getClaimScope(claim),
					})
				}
			>
				<Pencil aria-hidden="true" className="size-4" />
				Edit
			</DropdownMenuItem>
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11"
				onSelect={() => setClaimToDelete(claim)}
			>
				<Trash2 aria-hidden="true" className="size-4" />
				Delete
			</DropdownMenuItem>
		</RecordActionsMenu>
	);

	if (editing) {
		return (
			<div className="py-4">
				{editing.originalName === null && (
					<div className="mb-6 grid gap-2">
						<Label htmlFor="claim-organization">Organization</Label>
						<OrganizationSelect
							id="claim-organization"
							value={editing.scope}
							onChange={(scope) => {
								if (
									typeof scope !== "string" ||
									!authorization.canAt(
										"claims.readwrite",
										orgTarget(scope),
									)
								)
									return;
								setEditing({
									...editing,
									scope,
									claim: {
										...editing.claim,
										organization_id: scope,
									},
								});
							}}
							showGlobal={false}
							knownOrganizations={knownOrganizations}
							loadOrganizations={authorization.canAnywhere(
								"organizations.read",
							)}
							filterOrganizations={(organization) =>
								authorization.canAt(
									"claims.readwrite",
									orgTarget(organization.id),
								)
							}
						/>
					</div>
				)}
				<CustomClaimEditor
					value={editing.claim}
					onChange={(claim) => setEditing({ ...editing, claim })}
					onSave={handleSave}
					onCancel={() => setEditing(null)}
					nameDisabled={editing.originalName !== null}
				/>
			</div>
		);
	}

	return (
		<div className="flex min-h-0 max-h-full flex-col gap-0">
			<ListToolbar className="shrink-0 gap-0 border-b border-border/70 bg-muted/20 p-0">
				{canReadAnyClaims && (
					<div className="w-full shrink-0 border-b sm:w-56 sm:self-stretch sm:border-b-0 sm:border-r">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={false}
							knownOrganizations={knownOrganizations}
							loadOrganizations={authorization.canAnywhere(
								"organizations.read",
							)}
							filterOrganizations={(organization) =>
								authorization.canAt(
									"claims.read",
									orgTarget(organization.id),
								)
							}
							placeholder="All Organizations"
							triggerClassName="h-full min-h-12 rounded-none border-0 bg-transparent px-4 py-2 shadow-none hover:bg-muted/50 focus-visible:ring-inset"
						/>
					</div>
				)}
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					aria-label="Search custom claims"
					placeholder="Search custom claims by name or description..."
					className="min-w-40 flex-1 [&>input]:h-12 [&>input]:rounded-none [&>input]:border-0 [&>input]:bg-transparent [&>input]:shadow-none [&>input]:focus-visible:ring-inset"
				/>
				<div className="flex min-w-0 flex-wrap items-center gap-2 self-stretch pl-3 max-sm:w-full sm:ml-auto">
					<Button
						variant="ghost"
						size="icon"
						onClick={() => refresh()}
						title="Refresh"
						aria-label="Refresh claims"
						aria-busy={loading}
						disabled={loading}
						className="h-11 w-11 shrink-0 lg:h-10 lg:w-10"
					>
						<RefreshCw
							className={`h-4 w-4 ${loading ? "animate-spin motion-reduce:animate-none" : ""}`}
						/>
					</Button>
					{canManageAnyClaims && (
						<WorkspacePrimaryAction
							onClick={handleAdd}
							ref={addClaimRef}
							title="Add Claim"
							aria-label="Add Claim"
						>
							<Plus className="h-4 w-4" />
							New Claim
						</WorkspacePrimaryAction>
					)}
				</div>
			</ListToolbar>

			{loadError && (
				<Alert variant="destructive">
					<AlertTitle>Custom claims could not be loaded</AlertTitle>
					<AlertDescription className="space-y-3">
						<p>
							{hasLoaded
								? "Showing the last loaded claims. Refresh to get the latest changes."
								: "Try again to load your claims. Your search is preserved."}
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							disabled={loading}
							onClick={() => refresh()}
						>
							Retry claims
						</Button>
					</AlertDescription>
				</Alert>
			)}
			{initialLoading ? (
				<div
					role="status"
					aria-label="Loading custom claims"
					className="flex min-h-40 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground"
				>
					<Loader2
						aria-hidden="true"
						className="size-4 animate-spin motion-reduce:animate-none"
					/>
					Loading custom claims
				</div>
			) : loadError && !hasLoaded ? null : filteredClaims.length > 0 ? (
				<div className="flex min-h-0 max-h-full flex-col">
					{compactLayout ? (
						<ul
							aria-label="Custom claims"
							className="min-h-0 divide-y overflow-auto"
						>
							{filteredClaims.map((claim) => (
								<li key={claim.id} className="space-y-3 p-4">
									<h2 className="font-mono text-sm font-semibold [overflow-wrap:anywhere]">
										{claim.name}
									</h2>
									<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
										<MarkdownContent
											content={
												claim.description ||
												"No description"
											}
											variant="preview"
										/>
									</p>
									<dl className="grid gap-3 text-sm">
					{canReadAnyClaims && (
											<div>
												<dt className="text-xs text-muted-foreground">
													Organization
												</dt>
												<dd className="mt-1 [overflow-wrap:anywhere]">
													{getOrgName(
														claim.organization_id,
													)}
												</dd>
											</div>
										)}
										<div>
											<dt className="text-xs text-muted-foreground">
												Type
											</dt>
											<dd className="mt-1">
												{claim.type}
											</dd>
										</div>
										<div>
											<dt className="text-xs text-muted-foreground">
												Source table
											</dt>
											<dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">
												{claim.query.table}
											</dd>
										</div>
										<div>
											<dt className="text-xs text-muted-foreground">
												Select
											</dt>
											<dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">
												{claim.query.select}
											</dd>
										</div>
									</dl>
									{claim.is_solution_managed ? (
										<div className="flex flex-wrap items-center gap-2 border-t pt-3">
											<SolutionManagedBadge
												solutionId={claim.solution_id}
											/>
											<span className="text-xs text-muted-foreground">
												Edits are managed through
												deployment.
											</span>
										</div>
									) : canManageClaim(claim) ? (
										<div className="border-t pt-3">
											{renderClaimActions(claim)}
										</div>
									) : null}
								</li>
							))}
						</ul>
					) : (
						<DataTable className="max-h-full rounded-none border-0">
							<DataTableHeader>
								<DataTableRow>
									{canReadAnyClaims && (
										<DataTableHead className="w-0 whitespace-nowrap">
											Organization
										</DataTableHead>
									)}
									<DataTableHead>Name</DataTableHead>
									<DataTableHead>Description</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Type
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Source table
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Select
									</DataTableHead>
									{canManageAnyClaims && (
										<DataTableHead className="w-0 whitespace-nowrap text-right" />
									)}
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{filteredClaims.map((claim) => (
									<DataTableRow
										key={claim.id}
										className={
											claim.is_solution_managed || !canManageClaim(claim)
												? "hover:bg-muted/50"
												: "cursor-pointer hover:bg-muted/50"
										}
										onClick={() => {
											if (!canManageClaim(claim))
												return;
											setEditing({
												claim,
												originalName: claim.name,
												scope: getClaimScope(claim),
											});
										}}
									>
										{canReadAnyClaims && (
											<DataTableCell className="w-0 whitespace-nowrap">
												<Badge
													variant="outline"
													className="gap-1"
												>
													<Building2 className="h-3 w-3" />
													{getOrgName(
														claim.organization_id,
													)}
												</Badge>
											</DataTableCell>
										)}
										<DataTableCell className="font-mono font-medium">
											<div className="flex items-center gap-2">
												{claim.name}
												<SolutionManagedBadge
													solutionId={
														claim.solution_id
													}
												/>
											</div>
										</DataTableCell>
										<DataTableCell className="max-w-xs truncate text-muted-foreground">
											{claim.description || "-"}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
											{claim.type}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap font-mono text-sm">
											{claim.query.table}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap font-mono text-sm">
											{claim.query.select}
										</DataTableCell>
										{canManageAnyClaims && (
											<DataTableCell
												className="w-0 whitespace-nowrap text-right"
												onClick={(e) => e.stopPropagation()}
											>
												{canManageClaim(claim) &&
													renderClaimActions(claim)}
											</DataTableCell>
										)}
									</DataTableRow>
								))}
							</DataTableBody>
						</DataTable>
					)}
				</div>
			) : (
				<div className="flex min-h-48 flex-col items-center justify-center px-6 py-8 text-center">
					<KeyRound className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						{searchTerm
							? "No custom claims match your search"
							: "No custom claims yet"}
					</h3>
					<p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
						{searchTerm
							? "Try adjusting your search term or clear the filter"
							: "Custom claims are reusable query-resolved facts you can reference from table policies."}
					</p>
					{!searchTerm && canManageAnyClaims && (
						<Button
							variant="outline"
							onClick={handleAdd}
							className="mt-4"
						>
							<Plus className="size-4" />
							Create your first custom claim
						</Button>
					)}
				</div>
			)}

			{claimToDelete && (
				<ClaimDeleteDialog
					name={claimToDelete.name}
					onConfirm={handleDeleteConfirmed}
					onOpenChange={(open) => !open && setClaimToDelete(null)}
					returnFocusRef={addClaimRef}
				/>
			)}
		</div>
	);
}
