import { Pencil, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import type { CustomClaim } from "@/services/claims";

export interface CustomClaimsListProps {
	claims: CustomClaim[];
	onEdit: (name: string) => void;
	onDelete: (name: string) => void;
	onAdd: () => void;
}

export function CustomClaimsList({
	claims,
	onEdit,
	onDelete,
	onAdd,
}: CustomClaimsListProps) {
	return (
		<div className="space-y-3">
			<div className="flex justify-end">
				<Button type="button" onClick={onAdd}>
					<Plus className="h-4 w-4" />
					Add claim
				</Button>
			</div>

			{claims.length === 0 ? (
				<div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
					No custom claims yet.
				</div>
			) : (
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>Name</TableHead>
							<TableHead>Type</TableHead>
							<TableHead>Source table</TableHead>
							<TableHead>Select</TableHead>
							<TableHead className="w-[150px]" />
						</TableRow>
					</TableHeader>
					<TableBody>
						{claims.map((claim) => (
							<TableRow key={claim.name}>
								<TableCell className="font-medium">
									{claim.name}
								</TableCell>
								<TableCell>{claim.type}</TableCell>
								<TableCell>{claim.query.table}</TableCell>
								<TableCell>{claim.query.select}</TableCell>
								<TableCell>
									<div className="flex justify-end gap-1">
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => onEdit(claim.name)}
										>
											<Pencil className="h-4 w-4" />
											Edit
										</Button>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => onDelete(claim.name)}
										>
											<Trash2 className="h-4 w-4" />
											Delete
										</Button>
									</div>
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>
			)}
		</div>
	);
}
