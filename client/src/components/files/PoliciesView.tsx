import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";
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

interface PoliciesViewProps {
	scope: string | null;
	/** Bump to force a refetch after a policy mutation elsewhere. */
	refreshKey: number;
	onEdit: (policy: FilePolicy) => void;
}

/**
 * Flat list of every file policy in the current scope — the "manage all
 * policies" surface the per-item "Manage policy" modal can't provide. Lists
 * across all locations; each row opens the policy editor for that exact
 * (location, path).
 */
export function PoliciesView({ scope, refreshKey, onEdit }: PoliciesViewProps) {
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
		<div className="flex h-full min-h-0 flex-col">
			<div className="flex shrink-0 items-center justify-between px-3 py-2">
				<p className="text-sm font-medium">Policies</p>
				<Badge variant="secondary">{policies.length}</Badge>
			</div>
			<div className="min-h-0 flex-1 overflow-auto">
				{loading && policies.length === 0 ? (
					<p className="p-4 text-sm text-muted-foreground">Loading…</p>
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
								<DataTableHead>Location</DataTableHead>
								<DataTableHead>Path</DataTableHead>
								<DataTableHead>Rules</DataTableHead>
								<DataTableHead className="w-16 text-right">Edit</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{policies.map((policy) => (
								<DataTableRow
									key={policy.id ?? `${policy.location}:${policy.path}`}
									clickable
									onClick={() => onEdit(policy)}
								>
									<DataTableCell>
										<span className="font-mono text-xs">{policy.location}</span>
									</DataTableCell>
									<DataTableCell>
										<span className="font-mono text-xs">
											{policy.path || "(root)"}
										</span>
									</DataTableCell>
									<DataTableCell>
										<div className="flex flex-wrap gap-1">
											{policy.policies.policies.map((rule) => (
												<Badge key={rule.name} variant="outline">
													{rule.name}
												</Badge>
											))}
										</div>
									</DataTableCell>
									<DataTableCell>
										<div className="flex justify-end">
											<Button
												type="button"
												variant="ghost"
												size="icon-xs"
												aria-label={`Edit policy for ${policy.location}/${policy.path}`}
												onClick={(event) => {
													event.stopPropagation();
													onEdit(policy);
												}}
											>
												<Pencil className="h-3 w-3" />
											</Button>
										</div>
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</div>
		</div>
	);
}
