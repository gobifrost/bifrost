import { useCallback, useEffect, useMemo, useState } from "react";
import {
	Building2,
	KeyRound,
	Pencil,
	Plus,
	RefreshCw,
	Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { CustomClaimEditor } from "@/components/tables/CustomClaimEditor";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
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
import { Badge } from "@/components/ui/badge";
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
import { SearchBox } from "@/components/search/SearchBox";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useSearch } from "@/hooks/useSearch";
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
	const { isPlatformAdmin, user } = useAuth();
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	const [claims, setClaims] = useState<CustomClaim[]>([]);
	const [loading, setLoading] = useState(true);
	const [searchTerm, setSearchTerm] = useState("");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [editing, setEditing] = useState<EditingState | null>(null);
	const [claimToDelete, setClaimToDelete] = useState<CustomClaim | null>(null);

	const orgNameById = useMemo(() => {
		const m = new Map<string, string>();
		for (const o of organizations ?? []) m.set(o.id, o.name);
		return m;
	}, [organizations]);

	const getOrgName = (orgId?: string | null): string =>
		orgId ? orgNameById.get(orgId) ?? orgId : "Global";

	const getClaimScope = (claim: CustomClaim): string | undefined =>
		claim.organization_id ?? undefined;

	const apiScope =
		filterOrgId === undefined || filterOrgId === null
			? undefined
			: filterOrgId;

	const fetchClaims = useCallback(async (signal?: AbortSignal) => {
		const list = await listClaims({ signal, scope: apiScope });
		setClaims(list.claims ?? []);
	}, [apiScope]);

	const refresh = useCallback(async () => {
		setLoading(true);
		try {
			await fetchClaims();
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to load custom claims",
			);
		} finally {
			setLoading(false);
		}
	}, [fetchClaims]);

	useEffect(() => {
		const controller = new AbortController();
		void (async () => {
			setLoading(true);
			try {
				await fetchClaims(controller.signal);
			} catch (error) {
				if (controller.signal.aborted) return;
				toast.error(
					error instanceof Error
						? error.message
						: "Failed to load custom claims",
				);
			} finally {
				if (!controller.signal.aborted) {
					setLoading(false);
				}
			}
		})();
		return () => controller.abort();
	}, [fetchClaims]);

	const filteredClaims = useSearch(claims, searchTerm, [
		"name",
		"description",
	]);

	async function handleSave(claim: CustomClaim) {
		try {
			if (editing?.originalName) {
				await updateClaim(
					editing.originalName,
					{
						description: claim.description,
						type: claim.type,
						query: claim.query,
					},
					{ scope: editing.scope },
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
					{ scope: editing?.scope },
				);
				toast.success("Claim created");
			}
			setEditing(null);
			await refresh();
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : "Failed to save claim",
			);
		}
	}

	async function handleDeleteConfirmed() {
		if (!claimToDelete) return;
		try {
			await deleteClaim(claimToDelete.name, {
				scope: getClaimScope(claimToDelete),
			});
			toast.success("Claim deleted");
			setClaimToDelete(null);
			await refresh();
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to delete claim",
			);
		}
	}

	function handleAdd() {
		const defaultOrg = user?.organizationId ?? "";
		setEditing({
			claim: { ...EMPTY_CLAIM, organization_id: defaultOrg },
			originalName: null,
			scope: defaultOrg,
		});
	}

	if (editing) {
		return (
			<div className="py-4">
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
		<div className="flex flex-1 min-h-0 flex-col space-y-6">
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search custom claims by name or description..."
					className="flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={false}
							placeholder="All organizations"
						/>
					</div>
				)}
				<div className="ml-auto flex items-center gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refresh()}
						title="Refresh"
						aria-label="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						size="icon"
						onClick={handleAdd}
						title="Add Claim"
						aria-label="Add Claim"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{loading ? (
				<div className="space-y-2">
					{[...Array(5)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : filteredClaims.length > 0 ? (
				<div className="flex-1 min-h-0">
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								{isPlatformAdmin && (
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
								<DataTableHead className="w-0 whitespace-nowrap text-right" />
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredClaims.map((claim) => (
									<DataTableRow
										key={claim.id}
										className={
											claim.is_solution_managed
												? "hover:bg-muted/50"
												: "cursor-pointer hover:bg-muted/50"
										}
										onClick={() => {
											if (claim.is_solution_managed) return;
											setEditing({
												claim,
												originalName: claim.name,
												scope: getClaimScope(claim),
											});
										}}
									>
										{isPlatformAdmin && (
											<DataTableCell className="w-0 whitespace-nowrap">
												<Badge variant="outline" className="gap-1">
													<Building2 className="h-3 w-3" />
													{getOrgName(claim.organization_id)}
												</Badge>
											</DataTableCell>
										)}
									<DataTableCell className="font-mono font-medium">
										<div className="flex items-center gap-2">
											{claim.name}
											<SolutionManagedBadge
												solutionId={claim.solution_id}
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
									<DataTableCell
										className="w-0 whitespace-nowrap text-right"
										onClick={(e) => e.stopPropagation()}
									>
										{!claim.is_solution_managed && (
											<div className="flex justify-end gap-2">
												<Button
														variant="ghost"
														size="icon"
														onClick={() =>
															setEditing({
																claim,
																originalName: claim.name,
																scope: getClaimScope(claim),
															})
														}
														title="Edit claim"
														aria-label="Edit claim"
												>
													<Pencil className="h-4 w-4" />
												</Button>
												<Button
													variant="ghost"
													size="icon"
													onClick={() =>
														setClaimToDelete(claim)
													}
													title="Delete claim"
													aria-label="Delete claim"
												>
													<Trash2 className="h-4 w-4" />
												</Button>
											</div>
										)}
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				</div>
			) : (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12 text-center">
						<KeyRound className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm
								? "No custom claims match your search"
								: "No custom claims yet"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm
								? "Try adjusting your search term or clear the filter"
								: "Custom claims are reusable query-resolved facts you can reference from table policies."}
						</p>
						{!searchTerm && (
							<Button
								variant="outline"
								onClick={handleAdd}
								className="mt-4"
							>
								<Plus className="mr-2 h-4 w-4" />
								Create your first custom claim
							</Button>
						)}
					</CardContent>
				</Card>
			)}

			<AlertDialog
				open={claimToDelete !== null}
				onOpenChange={(open) => !open && setClaimToDelete(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete custom claim</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently remove{" "}
							<span className="font-mono">{claimToDelete?.name}</span>.
							Any table policy referencing it will be rejected at save
							time until you remove the reference. This action cannot be
							undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDeleteConfirmed}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
