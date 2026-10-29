import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertCircle, X } from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/api-error";

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
import { useOrganizations } from "@/hooks/useOrganizations";
import { useRoles } from "@/hooks/useRoles";
import { UserLookupNotice } from "./UserLookupNotice";
import { useBulkUserOperation } from "@/hooks/useUsers";
import { GLOBAL_TARGET } from "@/lib/authorization";
import { cn } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";

import type { components } from "@/lib/v1";

type BulkUserResponse = components["schemas"]["BulkUserResponse"];

/** A selected row: a user, or an identity on the Identities list. */
export type BulkTarget = Pick<components["schemas"]["UserPublic"], "id"> & {
	name?: string | null;
	email?: string;
};

export interface BulkDialogSharedProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	users: BulkTarget[];
	/** Fires when the operation resolved with at least one failed entry. */
	onPartialFailure: (result: BulkUserResponse, users: BulkTarget[]) => void;
	/**
	 * Fires after a successful submit (any rows succeeded). Parent uses this
	 * to clear the row selection. Cancel/dismiss intentionally does NOT call
	 * this — the user keeps their selection if they back out.
	 */
	onSuccess?: () => void;
}

function summarize(result: BulkUserResponse, action: string) {
	if (result.failed.length === 0) {
		toast.success(`${action} (${result.succeeded.length})`);
	}
}

function BulkSubmitError({ message }: { message: string | null }) {
	const errorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (message) {
			errorRef.current?.focus();
			errorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [message]);
	if (!message) return null;

	return (
		<div
			ref={errorRef}
			role="alert"
			tabIndex={-1}
			className="mt-4 outline-none"
		>
			<div className="rounded-[var(--bf-radius-surface)] border border-[var(--bf-danger)]/20 bg-[var(--bf-danger-soft)] p-3 text-sm text-[var(--bf-danger)]">
				<div className="flex items-start gap-2">
					<AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
					<p className="min-w-0 break-words">{message}</p>
				</div>
			</div>
		</div>
	);
}

export function BulkDialogFrame({
	open,
	onOpenChange,
	title,
	description,
	children,
	footer,
	maxWidthClassName = "sm:max-w-lg",
	compact = false,
	busy = false,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	title: string;
	description: string;
	children: ReactNode;
	footer: ReactNode;
	maxWidthClassName?: string;
	compact?: boolean;
	busy?: boolean;
}) {
	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!busy) onOpenChange(nextOpen);
			}}
		>
			<DialogContent
				showCloseButton={false}
				onEscapeKeyDown={(event) => {
					if (busy) event.preventDefault();
				}}
				onInteractOutside={(event) => {
					if (busy) event.preventDefault();
				}}
				className={cn(
					"flex h-[100dvh] w-full max-w-none flex-col gap-0 overflow-hidden rounded-none border-border/70 p-0 shadow-xl motion-reduce:transition-none motion-reduce:animate-none sm:h-auto sm:max-h-[min(90vh,42rem)] sm:w-[min(92vw,42rem)] sm:rounded-[var(--bf-radius-feature)]",
					maxWidthClassName,
					compact &&
						"h-auto max-h-[90dvh] w-[calc(100vw-2rem)] rounded-[var(--bf-radius-feature)]",
				)}
			>
				<DialogHeader className="shrink-0 border-b border-border/70 px-4 pb-4 pt-[max(1rem,env(safe-area-inset-top))] text-left sm:px-6">
					<div className="flex items-start gap-3">
						<div className="min-w-0 flex-1">
							<DialogTitle className="text-pretty break-words">
								{title}
							</DialogTitle>
							<DialogDescription className="mt-1.5 text-sm leading-5">
								{description}
							</DialogDescription>
						</div>
						<Button
							type="button"
							variant="ghost"
							size="icon-lg"
							onClick={() => onOpenChange(false)}
							aria-label="Close dialog"
							disabled={busy}
							className="h-11 w-11 shrink-0 rounded-[var(--bf-radius-control)] border border-border/70 bg-background/90 text-foreground hover:bg-muted motion-reduce:transition-none"
						>
							<X className="h-5 w-5" />
						</Button>
					</div>
				</DialogHeader>
				<div
					inert={busy}
					aria-busy={busy}
					className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6"
				>
					{children}
				</div>
				<div className="shrink-0 border-t border-border/70 px-4 py-4 sm:px-6">
					{footer}
				</div>
			</DialogContent>
		</Dialog>
	);
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
	onSuccess,
}: BulkDialogSharedProps) {
	const [orgId, setOrgId] = useState<string | null | undefined>(undefined);
	const [submitError, setSubmitError] = useState<string | null>(null);
	const lookup = useOrganizations();
	const authorization = useAuthorization();
	const lookupReady = lookup.data !== undefined && !lookup.isError;
	const bulkOp = useBulkUserOperation();
	const submitBusy = useRef(false);

	const handleSubmit = async () => {
		if (submitBusy.current || !lookupReady) return;
		setSubmitError(null);
		// `undefined` means "no selection". Org select normalizes to null (= platform) or a UUID.
		if (orgId === undefined) {
			const message = "Choose a destination organization";
			setSubmitError(message);
			return;
		}
		submitBusy.current = true;
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
			if (result.succeeded.length > 0) onSuccess?.();
			onOpenChange(false);
		} catch (e) {
			const message = getErrorMessage(e, "Bulk move failed");
			setSubmitError(message);
		} finally {
			submitBusy.current = false;
		}
	};

	return (
		<BulkDialogFrame
			open={open}
			onOpenChange={onOpenChange}
			busy={bulkOp.isPending}
			title={`Move ${users.length} user(s) to organization`}
			description="Each user's organization will be set to the choice below. Platform admins moved to a non-provider org will be refused — they need to be demoted first."
			maxWidthClassName="sm:max-w-xl"
			footer={
				<DialogFooter>
					<Button
						variant="outline"
						disabled={bulkOp.isPending}
						onClick={() => onOpenChange(false)}
						className="h-11"
					>
						Cancel
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={bulkOp.isPending || !lookupReady}
						className="h-11"
					>
						{bulkOp.isPending ? "Moving..." : "Move users"}
					</Button>
				</DialogFooter>
			}
		>
			<UserLookupNotice
				resource="organizations"
				loading={lookup.isLoading}
				failed={lookup.isError}
				retrying={lookup.isFetching}
				onRetry={() => void lookup.refetch()}
			/>
			<div className="space-y-2">
				<Label htmlFor="bulk-org">Destination</Label>
				<OrganizationSelect
					id="bulk-org"
					disabled={!lookupReady || bulkOp.isPending}
					value={orgId}
					onChange={setOrgId}
					// A move needs authority at the destination too.
					showGlobal={authorization.canAt(
						"userlifecycle.readwrite",
						GLOBAL_TARGET,
					)}
					filterOrganizations={(org) =>
						authorization.canAt("userlifecycle.readwrite", {
							kind: "org",
							id: org.id,
						})
					}
					placeholder="Select organization..."
				/>
			</div>
			<BulkSubmitError message={submitError} />
		</BulkDialogFrame>
	);
}

// =============================================================================
// Replace roles
// =============================================================================

export interface BulkReplaceRolesDialogProps extends BulkDialogSharedProps {
	/** Who is selected: people on Users, identities on Identities. */
	subject?: "users" | "identities";
}

const REPLACE_ROLES_COPY = {
	users: {
		title: (count: number) => `Replace roles for ${count} user(s)`,
		description:
			"The selected roles below replace every user's current role set (overwrite, not additive). Your own account will be skipped.",
		empty: "No roles selected — submitting will clear every selected user's roles.",
	},
	identities: {
		title: (count: number) =>
			`Replace Roles for ${count} ${count === 1 ? "Identity" : "Identities"}`,
		description:
			"The selected roles replace each identity's additional roles (overwrite, not additive), placed in its own organization. Base roles stay.",
		empty: "No roles selected — submitting will remove every selected identity's additional roles.",
	},
};

export function BulkReplaceRolesDialog(props: BulkReplaceRolesDialogProps) {
	if (!props.open) return null;
	return <BulkReplaceRolesDialogInner {...props} />;
}

function BulkReplaceRolesDialogInner({
	open,
	onOpenChange,
	users,
	onPartialFailure,
	onSuccess,
	subject = "users",
}: BulkReplaceRolesDialogProps) {
	const copy = REPLACE_ROLES_COPY[subject];
	const [selected, setSelected] = useState<string[]>([]);
	const [submitError, setSubmitError] = useState<string | null>(null);
	const lookup = useRoles();
	const lookupReady = lookup.data !== undefined && !lookup.isError;
	const bulkOp = useBulkUserOperation();
	const submitBusy = useRef(false);

	const handleSubmit = async () => {
		if (submitBusy.current || !lookupReady) return;
		setSubmitError(null);
		submitBusy.current = true;
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
			if (result.succeeded.length > 0) onSuccess?.();
			onOpenChange(false);
		} catch (e) {
			const message = getErrorMessage(e, "Bulk role replace failed");
			setSubmitError(message);
		} finally {
			submitBusy.current = false;
		}
	};

	return (
		<BulkDialogFrame
			open={open}
			onOpenChange={onOpenChange}
			busy={bulkOp.isPending}
			title={copy.title(users.length)}
			description={copy.description}
			footer={
				<DialogFooter className="gap-3">
					<Button
						variant="outline"
						disabled={bulkOp.isPending}
						onClick={() => onOpenChange(false)}
						className="h-11"
					>
						Cancel
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={bulkOp.isPending || !lookupReady}
						className="h-11"
					>
						{bulkOp.isPending ? "Applying..." : "Replace roles"}
					</Button>
				</DialogFooter>
			}
		>
			<UserLookupNotice
				resource="roles"
				loading={lookup.isLoading}
				failed={lookup.isError}
				retrying={lookup.isFetching}
				onRetry={() => void lookup.refetch()}
			/>
			<div className="space-y-2">
				<Label htmlFor="bulk-roles">Roles</Label>
				<RolesMultiSelect
					disabled={!lookupReady || bulkOp.isPending}
					value={selected}
					onChange={setSelected}
				/>
			</div>

			{lookupReady && selected.length === 0 && (
				<div className="mt-3 flex items-start gap-2 rounded-[var(--bf-radius-surface)] border border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)] p-3 text-xs text-[var(--bf-warning)]">
					<AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
					<span>{copy.empty}</span>
				</div>
			)}
			<BulkSubmitError message={submitError} />
		</BulkDialogFrame>
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
	onSuccess,
}: BulkSetActiveDialogProps) {
	const bulkOp = useBulkUserOperation();
	const submitBusy = useRef(false);
	const [submitError, setSubmitError] = useState<string | null>(null);

	const handleSubmit = async () => {
		if (submitBusy.current) return;
		setSubmitError(null);
		submitBusy.current = true;
		try {
			const result = (await bulkOp.mutateAsync({
				body: {
					user_ids: users.map((u) => u.id),
					operation: "set_active",
					is_active: mode === "enable",
				},
			})) as BulkUserResponse;
			summarize(
				result,
				mode === "enable" ? "Enable users" : "Disable users",
			);
			if (result.failed.length > 0) onPartialFailure(result, users);
			if (result.succeeded.length > 0) onSuccess?.();
			onOpenChange(false);
		} catch (e) {
			const message = getErrorMessage(e, "Bulk set-active failed");
			setSubmitError(message);
		} finally {
			submitBusy.current = false;
		}
	};

	const verb = mode === "enable" ? "Enable" : "Disable";

	return (
		<BulkDialogFrame
			open={open}
			onOpenChange={onOpenChange}
			busy={bulkOp.isPending}
			title={`${verb} ${users.length} user(s)`}
			description={
				mode === "disable"
					? "Disabled users can't log in until re-enabled. Your own account will be skipped."
					: "Re-enable the selected users so they can log in again."
			}
			footer={
				<DialogFooter className="gap-3">
					<Button
						variant="outline"
						disabled={bulkOp.isPending}
						onClick={() => onOpenChange(false)}
						className="h-11"
					>
						Cancel
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={bulkOp.isPending}
						variant={mode === "disable" ? "destructive" : "default"}
						className="h-11"
					>
						{bulkOp.isPending
							? mode === "enable"
								? "Enabling..."
								: "Disabling..."
							: `${verb} users`}
					</Button>
				</DialogFooter>
			}
		>
			<div />
			<BulkSubmitError message={submitError} />
		</BulkDialogFrame>
	);
}

// =============================================================================
// Result dialog (partial-failure details)
// =============================================================================

export interface BulkResultDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	result: BulkUserResponse | null;
	users: BulkTarget[];
}

export function BulkResultDialog({
	open,
	onOpenChange,
	result,
	users,
}: BulkResultDialogProps) {
	const userById = useMemo(() => {
		const map = new Map<string, BulkTarget>();
		for (const u of users) map.set(u.id, u);
		return map;
	}, [users]);

	if (!result) return null;

	return (
		<BulkDialogFrame
			open={open}
			onOpenChange={onOpenChange}
			title="Bulk action results"
			compact
			description={`${result.succeeded.length} succeeded · ${result.failed.length} failed`}
			maxWidthClassName="sm:max-w-lg"
			footer={
				<DialogFooter className="gap-3">
					<Button
						onClick={() => onOpenChange(false)}
						className="h-11"
					>
						Close
					</Button>
				</DialogFooter>
			}
		>
			<div className="divide-y rounded-[var(--bf-radius-surface)] border border-border/70 bg-background">
				{result.failed.map((f) => {
					const u = userById.get(f.user_id);
					return (
						<div key={f.user_id} className="px-3 py-3 text-sm">
							<div className="font-medium">
								{u?.name || u?.email || f.user_id}
							</div>
							<div className="mt-1 text-xs text-muted-foreground">
								{f.reason}
							</div>
						</div>
					);
				})}
			</div>
		</BulkDialogFrame>
	);
}
