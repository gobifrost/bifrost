import { Link } from "react-router-dom";
import { ChevronLeft, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function RoleDetailHeader({
	name,
	description,
	isBuiltin,
	canManage,
	onEdit,
	onDelete,
}: {
	name: string;
	description?: string | null;
	isBuiltin: boolean;
	/** Edit and delete controls (roles.readwrite, custom roles only). */
	canManage: boolean;
	onEdit: () => void;
	onDelete: () => void;
}) {
	return (
		<header className="min-w-0 space-y-3">
			<Link
				to="/roles"
				className="inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
			>
				<ChevronLeft className="size-4" aria-hidden="true" />
				Roles
			</Link>
			<div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
				<div className="min-w-0 flex-1 [overflow-wrap:anywhere]">
					<h1 className="flex flex-wrap items-center gap-2 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
						{name}
						{isBuiltin && <Badge variant="outline">Built-in</Badge>}
					</h1>
					{description && (
						<p className="mt-2 text-sm text-muted-foreground">
							{description}
						</p>
					)}
					<p className="mt-2 text-sm text-muted-foreground">
						{isBuiltin
							? "Built-in roles come with Bifrost. Their permissions are fixed, and they can't be edited or deleted."
							: "Users assigned to this role can access the forms, agents, apps, workflows, and knowledge namespaces assigned below."}
					</p>
				</div>
				{canManage && (
					<div className="flex shrink-0 gap-2 [&>button]:min-h-11 [&>button]:flex-1 sm:[&>button]:flex-none">
						<Button variant="outline" onClick={onEdit}>
							<Pencil className="size-4" aria-hidden="true" />
							Edit
						</Button>
						<Button
							variant="outline"
							className="text-destructive hover:text-destructive"
							onClick={onDelete}
						>
							<Trash2 className="size-4" aria-hidden="true" />
							Delete
						</Button>
					</div>
				)}
			</div>
		</header>
	);
}
