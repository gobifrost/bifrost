import { useEffect, useRef, useState, type RefObject } from "react";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
	DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { authFetch } from "@/lib/api-client";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";

export function KnowledgeScopeDialog({
	documentIds,
	sourceOrganizationIds,
	onClose,
	onSaved,
	returnFocusRef,
}: {
	documentIds: string[];
	sourceOrganizationIds: Array<string | null>;
	onClose: () => void;
	onSaved: (updated: number) => void;
	returnFocusRef?: RefObject<HTMLElement | null>;
}) {
	const { user } = useAuth();
	const authorization = useAuthorization();
	const [scope, setScope] = useState<string | null | undefined>(null);
	const [conflict, setConflict] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);
	const busy = useRef(false);
	const errorRef = useRef<HTMLParagraphElement>(null);
	const returnFocus = useDialogReturnFocus(returnFocusRef, true);
	const canManageSourceScopes = sourceOrganizationIds.every(
		(organizationId) =>
			authorization.canAt(
				"knowledge.readwrite",
				orgTarget(organizationId),
			),
	);
	const canManageTargetScope = authorization.canAt(
		"knowledge.readwrite",
		orgTarget(scope),
	);
	const canSave = canManageSourceScopes && canManageTargetScope;
	const knownOrganizations = user?.organizationId
		? [{ id: user.organizationId, name: "Your organization" }]
		: [];
	useEffect(() => {
		if (error) {
			errorRef.current?.focus();
			errorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [error]);
	async function save(replace: boolean) {
		if (
			busy.current ||
			!documentIds.length ||
			scope === undefined ||
			!canSave
		)
			return;
		busy.current = true;
		setPending(true);
		setError(null);
		try {
			const response = await authFetch(
				"/api/knowledge-sources/documents/scope",
				{
					method: "PATCH",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						document_ids: documentIds,
						scope: scope === null ? "global" : scope,
						replace,
					}),
				},
			);
			const result = await response.json().catch(() => ({}));
			const detail =
				typeof result.detail === "string"
					? result.detail
					: result.detail?.message;
			if (response.status === 409) {
				setConflict(
					detail ||
						"Documents with matching keys already exist in this scope.",
				);
				return;
			}
			if (!response.ok)
				throw new Error(
					detail || "Could not update document scope. Try again.",
				);
			onSaved(result.updated);
			onClose();
		} catch (cause) {
			setError(
				cause instanceof Error
					? cause.message
					: "Could not update document scope. Try again.",
			);
		} finally {
			busy.current = false;
			setPending(false);
		}
	}
	return (
		<Dialog
			open
			onOpenChange={(open) => {
				if (!open && !busy.current) onClose();
			}}
		>
			<DialogContent
				{...returnFocus}
				className="max-h-[90dvh] overflow-y-auto sm:max-w-md"
				onEscapeKeyDown={(e) => {
					if (busy.current) e.preventDefault();
				}}
			>
				<DialogHeader>
					<DialogTitle>
						{conflict
							? "Replace Existing Documents?"
							: "Change Scope"}
					</DialogTitle>
					<DialogDescription>
						{conflict
							? `${conflict} Replace the existing documents with the selected documents?`
							: `Update the organization scope for ${documentIds.length} selected document${documentIds.length === 1 ? "" : "s"}.`}
					</DialogDescription>
				</DialogHeader>
				<div className="space-y-2">
					<Label htmlFor="knowledge-target-scope">
						Target Organization
					</Label>
					<OrganizationSelect
						id="knowledge-target-scope"
						value={scope}
						onChange={setScope}
						showGlobal
						knownOrganizations={knownOrganizations}
						loadOrganizations={authorization.canAnywhere(
							"organizations.read",
						)}
						disabled={pending || !!conflict}
					/>
				</div>
				{error && (
					<p
						ref={errorRef}
						tabIndex={-1}
						role="alert"
						className="text-sm text-destructive outline-none [overflow-wrap:anywhere]"
					>
						{error}
					</p>
				)}
				<DialogFooter>
					<Button
						variant="outline"
						disabled={pending}
						className="min-h-11"
						onClick={() =>
							conflict ? setConflict(null) : onClose()
						}
					>
						{conflict ? "Back" : "Cancel"}
					</Button>
					<Button
						variant={conflict ? "destructive" : "default"}
						disabled={pending || scope === undefined || !canSave}
						className="min-h-11"
						onClick={() => void save(!!conflict)}
					>
						{pending
							? "Updating…"
							: conflict
								? "Replace"
								: "Update Scope"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
