import { useEffect, useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
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
import { listFilePolicies, type FilePolicy } from "@/services/filePolicies";
import { InlineLoader } from "./InlineLoader";

interface PoliciesViewProps {
	scope: string | null;
	/** Bump to force a refetch after a policy mutation elsewhere. */
	refreshKey: number;
	onEdit: (policy: FilePolicy) => void;
	onDelete: (policy: FilePolicy) => void;
}

/**
 * Flat list of every file policy in the current scope — the "manage all
 * policies" surface the per-item "Manage policy" modal can't provide. Lists
 * across all locations; each row opens the policy editor for that exact
 * (location, path).
 */
export function PoliciesView({
	scope,
	refreshKey,
	onEdit,
	onDelete,
}: PoliciesViewProps) {
	const [policies, setPolicies] = useState<FilePolicy[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		void (async () => {
			setLoading(true);
			try {
				const result = await listFilePolicies({ scope: scope ?? undefined });
				if (!cancelled) {
					setPolicies(result.policies ?? []);
					setError(null);
				}
			} catch (err) {
				if (!cancelled)
					setError(err instanceof Error ? err.message : String(err));
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [scope, refreshKey]);

	return (
		<div className="h-full min-h-0 overflow-auto">
			{loading && policies.length === 0 ? (
				<InlineLoader className="p-4" />
			) : error ? (
				<p className="p-4 text-sm text-destructive">{error}</p>
			) : policies.length === 0 ? (
				<p className="p-4 text-sm text-muted-foreground">
					No policies in this scope yet.
				</p>
			) : (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							{/* Policy sizes to its content; Rules grows to fill. */}
							<DataTableHead className="w-px whitespace-nowrap">Policy</DataTableHead>
							<DataTableHead>Rules</DataTableHead>
							<DataTableHead className="w-px whitespace-nowrap text-right">Actions</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{policies.map((policy) => (
							<DataTableRow
								key={policy.id ?? `${policy.location}:${policy.path}`}
								clickable
								onClick={() => onEdit(policy)}
							>
								<DataTableCell className="w-px whitespace-nowrap align-middle">
									<div className="flex flex-col">
										<span className="font-medium">{policy.location}</span>
										<span className="font-mono text-xs text-muted-foreground">
											/{policy.path || ""}
										</span>
									</div>
								</DataTableCell>
								<DataTableCell className="align-middle">
									<div className="flex flex-wrap gap-1">
										{policy.policies.policies.length === 0 ? (
											<span className="text-xs text-muted-foreground">
												no rules
											</span>
										) : (
											policy.policies.policies.map((rule) => (
												<Badge
													key={rule.name}
													variant="secondary"
													className="font-normal"
												>
													{rule.name}
												</Badge>
											))
										)}
									</div>
								</DataTableCell>
								<DataTableCell className="w-px whitespace-nowrap align-middle">
									<div className="flex items-center justify-end gap-1">
										<Button
											type="button"
											variant="outline"
											size="icon-xs"
											title="Edit policy"
											aria-label={`Edit policy for ${policy.location}/${policy.path}`}
											onClick={(event) => {
												event.stopPropagation();
												onEdit(policy);
											}}
										>
											<Pencil className="h-3 w-3" />
										</Button>
										<Button
											type="button"
											variant="outline"
											size="icon-xs"
											title="Delete policy"
											aria-label={`Delete policy for ${policy.location}/${policy.path}`}
											onClick={(event) => {
												event.stopPropagation();
												onDelete(policy);
											}}
										>
											<Trash2 className="h-3 w-3" />
										</Button>
									</div>
								</DataTableCell>
							</DataTableRow>
						))}
					</DataTableBody>
				</DataTable>
			)}
		</div>
	);
}
