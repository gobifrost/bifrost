import { useEffect, useRef, useState } from "react";
import { AlertCircle, Building2, Loader2 } from "lucide-react";

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
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useOrganizations } from "@/hooks/useOrganizations";
import { getErrorMessage } from "@/lib/api-error";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";
import { useAuthorization } from "@/services/authorization";
import {
	TOKEN_LIFETIMES,
	VENDORS,
	useCreateIdentityProvider,
	vendorLabel,
	type IdentityProviderVendor,
	type TokenLifetimeDays,
} from "@/services/identity-providers";

export type IdentityProviderCreated =
	components["schemas"]["IdentityProviderCreated"];

const WRITE = "identityproviders.readwrite";

function NewIdentityProviderDialogContent({
	onOpenChange,
	onCreated,
}: {
	onOpenChange: (open: boolean) => void;
	onCreated: (created: IdentityProviderCreated) => void;
}) {
	const authorization = useAuthorization();
	const organizationsQuery = useOrganizations();
	const create = useCreateIdentityProvider();
	const [name, setName] = useState("");
	const [organizationId, setOrganizationId] = useState("");
	const [vendor, setVendor] = useState<IdentityProviderVendor>("entra");
	const [lifetime, setLifetime] = useState<TokenLifetimeDays>(365);
	const [external, setExternal] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const errorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (error) errorRef.current?.focus();
	}, [error]);

	const choices: ComboboxOption[] = (organizationsQuery.data ?? [])
		.filter((org) => authorization.canAt(WRITE, orgTarget(org.id)))
		.sort((a, b) => a.name.localeCompare(b.name))
		.map((org) => ({ value: org.id, label: org.name, icon: Building2 }));
	const busy = create.isPending;
	const ready = name.trim().length > 0 && organizationId !== "";

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!ready || busy) return;
		setError(null);
		try {
			const created = await create.mutateAsync({
				body: {
					name: name.trim(),
					organization_id: organizationId,
					vendor,
					source_type: "scim",
					token_lifetime_days: lifetime,
					provision_as_external: external,
				},
			});
			onOpenChange(false);
			onCreated(created);
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The connection could not be created."),
			);
		}
	};

	return (
		<DialogContent
			onEscapeKeyDown={(event) => busy && event.preventDefault()}
			onInteractOutside={(event) => busy && event.preventDefault()}
			className="sm:max-w-lg"
		>
			<DialogHeader>
				<DialogTitle>New Identity Provider</DialogTitle>
				<DialogDescription>
					Connect an organization's directory over SCIM. Bifrost
					creates the connection and its first token; you paste both
					into the identity provider.
				</DialogDescription>
			</DialogHeader>
			<form onSubmit={handleSubmit} className="space-y-4">
				{error && (
					<Alert variant="destructive" ref={errorRef} tabIndex={-1}>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				)}
				<div className="space-y-2">
					<Label htmlFor="idp-name">Display Name</Label>
					<Input
						id="idp-name"
						value={name}
						onChange={(event) => setName(event.target.value)}
						placeholder="Contoso Entra ID"
						maxLength={255}
						required
					/>
				</div>
				<div className="space-y-2">
					<Label htmlFor="idp-organization">Organization</Label>
					<Combobox
						id="idp-organization"
						value={organizationId}
						onValueChange={setOrganizationId}
						options={choices}
						isLoading={organizationsQuery.isLoading}
						placeholder="Select an organization..."
						searchPlaceholder="Search organizations..."
						emptyText="No organizations found."
					/>
					<p className="text-xs text-muted-foreground">
						Users are provisioned into this organization, on its
						verified email domains only.
					</p>
				</div>
				<div className="grid gap-4 sm:grid-cols-2">
					<div className="space-y-2">
						<Label htmlFor="idp-vendor">Provider</Label>
						<Select
							value={vendor}
							onValueChange={(value) =>
								setVendor(value as IdentityProviderVendor)
							}
						>
							<SelectTrigger id="idp-vendor" className="w-full">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{VENDORS.map((item) => (
									<SelectItem key={item} value={item}>
										{vendorLabel(item)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
					<div className="space-y-2">
						<Label htmlFor="idp-lifetime">Token Lifetime</Label>
						<Select
							value={String(lifetime)}
							onValueChange={(value) =>
								setLifetime(Number(value) as TokenLifetimeDays)
							}
						>
							<SelectTrigger id="idp-lifetime" className="w-full">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{TOKEN_LIFETIMES.map((days) => (
									<SelectItem key={days} value={String(days)}>
										{days} days
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
				</div>
				<div className="flex items-start gap-3">
					<Switch
						id="idp-external"
						checked={external}
						onCheckedChange={setExternal}
					/>
					<div className="space-y-1">
						<Label htmlFor="idp-external">
							Provision as External Users
						</Label>
						<p className="text-xs text-muted-foreground">
							External users see only what a role or an explicit
							grant gives them, not what's shared with every
							signed-in user. Use this for portal or guest
							accounts.
						</p>
					</div>
				</div>
				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						disabled={busy}
						onClick={() => onOpenChange(false)}
					>
						Cancel
					</Button>
					<Button type="submit" disabled={!ready || busy}>
						{busy && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						Create Connection
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function NewIdentityProviderDialog({
	open,
	onOpenChange,
	onCreated,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated: (created: IdentityProviderCreated) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<NewIdentityProviderDialogContent
					onOpenChange={onOpenChange}
					onCreated={onCreated}
				/>
			)}
		</Dialog>
	);
}
