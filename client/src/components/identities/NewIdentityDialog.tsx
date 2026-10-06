import { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getErrorMessage } from "@/lib/api-error";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import { useCreateIdentity, type Identity } from "@/services/identities";

const LIFECYCLE = "users.lifecycle.readwrite";

function NewIdentityDialogContent({
	onOpenChange,
	onCreated,
}: {
	onOpenChange: (open: boolean) => void;
	onCreated: (identity: Identity) => void;
}) {
	const [name, setName] = useState("");
	// undefined until chosen; null is Global.
	const [organizationId, setOrganizationId] = useState<
		string | null | undefined
	>(undefined);
	const [error, setError] = useState<string | null>(null);
	const errorRef = useRef<HTMLDivElement>(null);
	const authorization = useAuthorization();
	const create = useCreateIdentity();
	useEffect(() => {
		if (error) errorRef.current?.focus();
	}, [error]);

	const ready = name.trim().length > 0 && organizationId !== undefined;
	const busy = create.isPending;

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!ready || busy || organizationId === undefined) return;
		setError(null);
		try {
			const identity = await create.mutateAsync({
				body: { name: name.trim(), organization_id: organizationId },
			});
			toast.success("Identity created", {
				description: `${identity.name} runs with the User base role until you give it more`,
			});
			onOpenChange(false);
			onCreated(identity);
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The identity could not be created."),
			);
		}
	};

	return (
		<DialogContent
			onEscapeKeyDown={(event) => {
				if (busy) event.preventDefault();
			}}
			onInteractOutside={(event) => {
				if (busy) event.preventDefault();
			}}
			showCloseButton={false}
			className="flex h-[100dvh] w-full max-w-none flex-col gap-0 overflow-hidden rounded-none border-border/70 p-0 shadow-xl motion-reduce:transition-none motion-reduce:animate-none sm:h-auto sm:max-h-[min(90dvh,44rem)] sm:w-[min(92vw,500px)] sm:rounded-[var(--bf-radius-feature)]"
		>
			<DialogHeader className="shrink-0 border-b border-border/70 px-4 pb-4 pt-[max(1rem,env(safe-area-inset-top))] text-left sm:px-6">
				<div className="flex items-start gap-3">
					<div className="min-w-0 flex-1">
						<DialogTitle className="text-pretty break-words">
							New Identity
						</DialogTitle>
						<DialogDescription className="mt-1.5 text-sm leading-5">
							An identity runs workflows that no person starts. It
							begins with the User base role; give it roles on its
							page.
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
			<form
				onSubmit={handleSubmit}
				className="flex min-h-0 flex-1 flex-col overflow-hidden"
			>
				<div
					inert={busy}
					aria-busy={busy}
					className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-6"
				>
					{error && (
						<Alert
							variant="destructive"
							ref={errorRef}
							tabIndex={-1}
							className="outline-none"
						>
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<div className="space-y-2">
						<Label htmlFor="identity-name">Name</Label>
						<Input
							id="identity-name"
							value={name}
							onChange={(event) => setName(event.target.value)}
							placeholder="Contoso Ticket Sync"
							maxLength={255}
							required
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="identity-organization">
							Organization
						</Label>
						<OrganizationSelect
							id="identity-organization"
							label="Organization"
							value={organizationId}
							onChange={setOrganizationId}
							showGlobal={authorization.canAt(
								LIFECYCLE,
								GLOBAL_TARGET,
							)}
							filterOrganizations={(organization) =>
								authorization.canAt(
									LIFECYCLE,
									orgTarget(organization.id),
								)
							}
							placeholder="Select an organization..."
						/>
						<p className="text-xs text-muted-foreground">
							Where its base role applies. A Global identity runs
							work that belongs to no organization.
						</p>
					</div>
				</div>
				<DialogFooter className="shrink-0 border-t border-border/70 px-4 py-4 sm:px-6">
					<Button
						type="button"
						variant="outline"
						className="h-11"
						disabled={busy}
						onClick={() => onOpenChange(false)}
					>
						Cancel
					</Button>
					<Button
						type="submit"
						className="h-11"
						disabled={!ready || busy}
					>
						{busy && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						Create Identity
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

/** Name and organization for a new custom identity. */
export function NewIdentityDialog({
	open,
	onOpenChange,
	onCreated,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated: (identity: Identity) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<NewIdentityDialogContent
					onOpenChange={onOpenChange}
					onCreated={onCreated}
				/>
			)}
		</Dialog>
	);
}
