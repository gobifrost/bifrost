import { UserPlus } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useUsers } from "@/hooks/useUsers";
import { useAssignUsersToRole } from "@/hooks/useRoles";
import { useMultiSelect } from "@/hooks/useMultiSelect";
import type { components } from "@/lib/v1";
type Role = components["schemas"]["RolePublic"];
type User = components["schemas"]["UserPublic"];

interface AssignUsersDialogProps {
	role?: Role | undefined;
	open: boolean;
	onClose: () => void;
}

export function AssignUsersDialog({
	role,
	open,
	onClose,
}: AssignUsersDialogProps) {
	const { selectedIds, toggle, clear, isSelected, count } = useMultiSelect();

	// Fetch users (filtered by scope server-side).
	const { data: users, isLoading } = useUsers();
	const assignUsers = useAssignUsersToRole();

	const handleAssign = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!role || count === 0) return;

		await assignUsers.mutateAsync({
			params: { path: { role_id: role.id } },
			body: { user_ids: selectedIds },
		});

		clear();
		onClose();
	};

	const handleClose = () => {
		clear();
		onClose();
	};

	if (!role) return null;

	return (
		<Dialog open={open} onOpenChange={handleClose}>
			<DialogContent className="sm:max-w-[600px]">
				<form onSubmit={handleAssign}>
					<DialogHeader>
						<DialogTitle>Assign Users to Role</DialogTitle>
						<DialogDescription>
							Select organization users to assign to "{role.name}"
						</DialogDescription>
					</DialogHeader>

					<div className="max-h-[400px] overflow-y-auto py-4">
						{isLoading ? (
							<div className="space-y-2">
								{[...Array(5)].map((_, i) => (
									<Skeleton key={i} className="h-16 w-full" />
								))}
							</div>
						) : users && users.length > 0 ? (
							<div className="space-y-2">
								{users.map((user: User) => {
									const selected = isSelected(user.id);
									return (
										<button
											key={user.id}
											onClick={() => toggle(user.id)}
											className={`w-full rounded-lg border p-4 text-left transition-colors ${
												selected
													? "border-primary bg-primary/5"
													: "border-border hover:bg-accent"
											}`}
										>
											<div className="flex items-center justify-between">
												<div>
													<p className="font-medium">
														{user.name}
													</p>
													<p className="text-sm text-muted-foreground">
														{user.email}
													</p>
												</div>
												{selected && (
													<Badge>Selected</Badge>
												)}
											</div>
										</button>
									);
								})}
							</div>
						) : (
							<div className="flex flex-col items-center justify-center py-8 text-center">
								<UserPlus className="h-12 w-12 text-muted-foreground" />
								<p className="mt-2 text-sm text-muted-foreground">
									No organization users available
								</p>
							</div>
						)}
					</div>

					<DialogFooter>
						<Button
							type="button"
							variant="outline"
							onClick={handleClose}
						>
							Cancel
						</Button>
						<Button
							type="submit"
							disabled={count === 0 || assignUsers.isPending}
						>
							{assignUsers.isPending
								? "Assigning..."
								: `Assign ${count} User${count !== 1 ? "s" : ""}`}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
