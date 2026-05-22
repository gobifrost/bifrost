import { useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";
import { toast } from "sonner";

import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { RolesMultiSelect } from "@/components/forms/RolesMultiSelect";
import { useBulkUserOperation } from "@/hooks/useUsers";

import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type BulkUserResponse = components["schemas"]["BulkUserResponse"];

export interface BulkDialogSharedProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	users: User[];
	/** Fires when the operation resolved with at least one failed entry. */
	onPartialFailure: (result: BulkUserResponse, users: User[]) => void;
}

function summarize(result: BulkUserResponse, action: string) {
	if (result.failed.length === 0) {
		toast.success(`${action} (${result.succeeded.length})`);
	} else if (result.succeeded.length === 0) {
		toast.error(`${action} failed`, {
			description: `${result.failed.length} user(s) could not be updated`,
		});
	} else {
		toast.warning(`${action} partially completed`, {
			description: `${result.succeeded.length} succeeded · ${result.failed.length} failed`,
		});
	}
}

// =============================================================================
// Move organization
// =============================================================================

export function BulkMoveOrgDialog(props: BulkDialogSharedProps) {
	// Remount on open so internal state resets without a useEffect.
	if (!props.open) return null;
	return <BulkMoveOrgDialogInner {...props} />;
}

function BulkMoveOrgDialogInner({
	open,
	onOpenChange,
	users,
	onPartialFailure,
}: BulkDialogSharedProps) {
	const [orgId, setOrgId] = useState<string | null | undefined>(undefined);
	const bulkOp = useBulkUserOperation();

	const handleSubmit = async () => {
		// `undefined` means "no selection". Org select normalizes to null (= platform) or a UUID.
		if (orgId === undefined) {
			toast.error("Choose a destination organization");
			return;
		}
		try {
			const result = (await bulkOp.mutateAsync({
				body: {
					user_ids: users.map((u) => u.id),
					operation: "move_org",
					organization_id: orgId,
				},
			})) as BulkUserResponse;
			summarize(result, "Move to org");
			if (result.failed.length > 0) onPartialFailure(result, users);
			onOpenChange(false);
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Bulk move failed",
			);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Move {users.length} user(s) to organization</DialogTitle>
					<DialogDescription>
						Each user's organization will be set to the choice below. Platform
						admins moved to a non-provider org will be refused — they need to be
						demoted first.
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-2">
					<Label htmlFor="bulk-org">Destination</Label>
					<OrganizationSelect
						value={orgId}
						onChange={setOrgId}
						showGlobal={true}
						placeholder="Select organization..."
					/>
				</div>

				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						Cancel
					</Button>
					<Button onClick={handleSubmit} disabled={bulkOp.isPending}>
						{bulkOp.isPending ? "Moving..." : "Move users"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

// =============================================================================
// Replace roles
// =============================================================================

export function BulkReplaceRolesDialog(props: BulkDialogSharedProps) {
	if (!props.open) return null;
	return <BulkReplaceRolesDialogInner {...props} />;
}

function BulkReplaceRolesDialogInner({
	open,
	onOpenChange,
	users,
	onPartialFailure,
}: BulkDialogSharedProps) {
	const [selected, setSelected] = useState<string[]>([]);
	const bulkOp = useBulkUserOperation();

	const handleSubmit = async () => {
		try {
			const result = (await bulkOp.mutateAsync({
				body: {
					user_ids: users.map((u) => u.id),
					operation: "replace_roles",
					role_ids: selected,
				},
			})) as BulkUserResponse;
			summarize(result, "Replace roles");
			if (result.failed.length > 0) onPartialFailure(result, users);
			onOpenChange(false);
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Bulk role replace failed",
			);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-lg">
				<DialogHeader>
					<DialogTitle>Replace roles for {users.length} user(s)</DialogTitle>
					<DialogDescription>
						The selected roles below replace every user's current role set
						(overwrite, not additive). Your own account will be skipped.
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-2">
					<Label htmlFor="bulk-roles">Roles</Label>
					<RolesMultiSelect value={selected} onChange={setSelected} />
				</div>

				{selected.length === 0 && (
					<div className="flex items-start gap-2 text-xs text-muted-foreground">
						<AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
						<span>
							No roles selected — submitting will clear every selected user's
							roles.
						</span>
					</div>
				)}

				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						Cancel
					</Button>
					<Button onClick={handleSubmit} disabled={bulkOp.isPending}>
						{bulkOp.isPending ? "Applying..." : "Replace roles"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

// =============================================================================
// Set active (disable / enable)
// =============================================================================

export interface BulkSetActiveDialogProps extends BulkDialogSharedProps {
	mode: "disable" | "enable";
}

export function BulkSetActiveDialog({
	open,
	onOpenChange,
	users,
	mode,
	onPartialFailure,
}: BulkSetActiveDialogProps) {
	const bulkOp = useBulkUserOperation();

	const handleSubmit = async () => {
		try {
			const result = (await bulkOp.mutateAsync({
				body: {
					user_ids: users.map((u) => u.id),
					operation: "set_active",
					is_active: mode === "enable",
				},
			})) as BulkUserResponse;
			summarize(result, mode === "enable" ? "Enable users" : "Disable users");
			if (result.failed.length > 0) onPartialFailure(result, users);
			onOpenChange(false);
		} catch (e) {
			toast.error(
				e instanceof Error ? e.message : "Bulk set-active failed",
			);
		}
	};

	const verb = mode === "enable" ? "Enable" : "Disable";

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>
						{verb} {users.length} user(s)
					</DialogTitle>
					<DialogDescription>
						{mode === "disable"
							? "Disabled users can't log in until re-enabled. Your own account will be skipped."
							: "Re-enable the selected users so they can log in again."}
					</DialogDescription>
				</DialogHeader>

				<DialogFooter>
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						Cancel
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={bulkOp.isPending}
						variant={mode === "disable" ? "destructive" : "default"}
					>
						{bulkOp.isPending ? `${verb}ing...` : `${verb} users`}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

// =============================================================================
// Result dialog (partial-failure details)
// =============================================================================

export interface BulkResultDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	result: BulkUserResponse | null;
	users: User[];
}

export function BulkResultDialog({
	open,
	onOpenChange,
	result,
	users,
}: BulkResultDialogProps) {
	const userById = useMemo(() => {
		const map = new Map<string, User>();
		for (const u of users) map.set(u.id, u);
		return map;
	}, [users]);

	if (!result) return null;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-lg">
				<DialogHeader>
					<DialogTitle>Bulk action results</DialogTitle>
					<DialogDescription>
						{result.succeeded.length} succeeded · {result.failed.length} failed
					</DialogDescription>
				</DialogHeader>

				<div className="max-h-80 overflow-y-auto border rounded divide-y">
					{result.failed.map((f) => {
						const u = userById.get(f.user_id);
						return (
							<div key={f.user_id} className="px-3 py-2 text-sm">
								<div className="font-medium">
									{u?.name || u?.email || f.user_id}
								</div>
								<div className="text-xs text-muted-foreground">
									{f.reason}
								</div>
							</div>
						);
					})}
				</div>

				<DialogFooter>
					<Button onClick={() => onOpenChange(false)}>Close</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
