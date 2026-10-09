import { useEffect, useRef, useState } from "react";
import { AlertCircle, Building2, Globe, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
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
import { useOrganizations } from "@/hooks/useOrganizations";
import { getErrorMessage } from "@/lib/api-error";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import {
	identityLabel,
	useCreateIdentity,
	useIdentities,
	type Identity,
} from "@/services/identities";

const LIFECYCLE = "users.lifecycle.readwrite";
const GLOBAL_CHOICE = "global";

/**
 * Where the caller may create an identity: Global, then each organization
 * they can create users in, by name. Organizations come from the identities
 * list (every organization has a default identity, listed wherever the caller
 * can read users) and, for a caller who can read organizations, from the
 * organizations list, since creating needs neither read permission.
 */
function placeChoices(
	organizations: { id: string | null; name: string | null }[],
	canCreateAt: (organizationId: string | null) => boolean,
): ComboboxOption[] {
	const named = new Map<string, string>();
	for (const { id, name } of organizations) {
		if (id && !named.has(id) && canCreateAt(id)) named.set(id, name ?? id);
	}
	return [
		...(canCreateAt(null)
			? [{ value: GLOBAL_CHOICE, label: "Global", icon: Globe }]
			: []),
		...[...named]
			.sort(([, a], [, b]) => a.localeCompare(b))
			.map(([id, name]) => ({ value: id, label: name, icon: Building2 })),
	];
}

function NewIdentityDialogContent({
	onOpenChange,
	onCreated,
}: {
	onOpenChange: (open: boolean) => void;
	onCreated: (identity: Identity) => void;
}) {
	const [name, setName] = useState("");
	// An organization id, GLOBAL_CHOICE, or "" until chosen.
	const [place, setPlace] = useState("");
	const [error, setError] = useState<string | null>(null);
	const errorRef = useRef<HTMLDivElement>(null);
	const authorization = useAuthorization();
	const create = useCreateIdentity();
	const identitiesQuery = useIdentities();
	const organizationsQuery = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	});
	const choices = placeChoices(
		[
			...(identitiesQuery.data ?? []).map((identity) => ({
				id: identity.organization_id,
				name: identity.organization_name,
			})),
			...(organizationsQuery.data ?? []),
		],
		(organizationId) =>
			authorization.canAt(
				LIFECYCLE,
				organizationId ? orgTarget(organizationId) : GLOBAL_TARGET,
			),
	);
	useEffect(() => {
		if (error) errorRef.current?.focus();
	}, [error]);

	const ready = name.trim().length > 0 && place !== "";
	const busy = create.isPending;

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!ready || busy) return;
		setError(null);
		try {
			const identity = await create.mutateAsync({
				body: {
					name: name.trim(),
					organization_id: place === GLOBAL_CHOICE ? null : place,
				},
			});
			toast.success("Identity created", {
				description: `${identityLabel(identity)} runs with the User base role until you give it more`,
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
						<Combobox
							id="identity-organization"
							value={place}
							onValueChange={setPlace}
							options={choices}
							isLoading={
								identitiesQuery.isLoading ||
								organizationsQuery.isLoading
							}
							placeholder="Select an organization..."
							searchPlaceholder="Search organizations..."
							emptyText="No organizations found."
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
