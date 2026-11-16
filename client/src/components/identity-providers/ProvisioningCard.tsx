import { useState } from "react";
import { Loader2, RotateCw } from "lucide-react";
import { toast } from "sonner";

import { HmacSecretReveal } from "@/components/forms/HmacSecretReveal";
import { CopyField } from "@/components/identity-providers/CopyField";
import {
	CredentialExpiryBadge,
	ProvisioningStatusBadge,
} from "@/components/identity-providers/IdentityProviderBadges";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { getErrorMessage } from "@/lib/api-error";
import { formatDate } from "@/lib/utils";
import {
	TOKEN_LIFETIMES,
	useRotateIdentityProviderToken,
	type IdentityProvider,
	type IdentityProviderTokenIssued,
	type TokenLifetimeDays,
} from "@/services/identity-providers";

function Row({
	label,
	children,
}: {
	label: string;
	children: React.ReactNode;
}) {
	return (
		<div className="grid gap-1 sm:grid-cols-[12rem_minmax(0,1fr)] sm:items-center">
			<dt className="text-sm text-muted-foreground">{label}</dt>
			<dd className="min-w-0 text-sm">{children}</dd>
		</div>
	);
}

function RotateTokenDialog({
	provider,
	open,
	onOpenChange,
	onIssued,
}: {
	provider: IdentityProvider;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onIssued: (issued: IdentityProviderTokenIssued) => void;
}) {
	const rotate = useRotateIdentityProviderToken();
	const [lifetime, setLifetime] = useState<TokenLifetimeDays>(365);
	const submit = async () => {
		try {
			const issued = await rotate.mutateAsync({
				params: { path: { identity_provider_id: provider.id } },
				body: { lifetime_days: lifetime },
			});
			onOpenChange(false);
			onIssued(issued);
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The token could not be regenerated."),
			);
		}
	};
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>Regenerate Token</DialogTitle>
					<DialogDescription>
						The current token keeps working for one hour, so you
						have time to paste the new one into the identity
						provider and test the connection.
					</DialogDescription>
				</DialogHeader>
				<div className="space-y-2">
					<Label htmlFor="rotate-lifetime">Token Lifetime</Label>
					<Select
						value={String(lifetime)}
						onValueChange={(value) =>
							setLifetime(Number(value) as TokenLifetimeDays)
						}
					>
						<SelectTrigger id="rotate-lifetime" className="w-full">
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
				<DialogFooter>
					<Button
						variant="outline"
						disabled={rotate.isPending}
						onClick={() => onOpenChange(false)}
					>
						Cancel
					</Button>
					<Button
						disabled={rotate.isPending}
						onClick={() => void submit()}
					>
						{rotate.isPending && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						Regenerate Token
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/**
 * What to paste into the identity provider (Tenant URL and token) and how
 * the connection is doing. A newly issued token is shown here once.
 */
export function ProvisioningCard({
	provider,
	canWrite,
	issuedToken,
	onDismissToken,
	onIssued,
}: {
	provider: IdentityProvider;
	canWrite: boolean;
	issuedToken: IdentityProviderTokenIssued | null;
	onDismissToken: () => void;
	onIssued: (issued: IdentityProviderTokenIssued) => void;
}) {
	const [rotateOpen, setRotateOpen] = useState(false);
	return (
		<Card>
			<CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
				<div className="space-y-1.5">
					<CardTitle>
						System for Cross-domain Identity Management (SCIM)
					</CardTitle>
					<CardDescription>
						Paste the Tenant URL and token into the identity
						provider's provisioning settings.
					</CardDescription>
				</div>
				{canWrite && (
					<Button
						variant="outline"
						onClick={() => setRotateOpen(true)}
					>
						<RotateCw
							aria-hidden="true"
							className="mr-1.5 h-4 w-4"
						/>
						Regenerate Token
					</Button>
				)}
			</CardHeader>
			<CardContent className="space-y-4">
				{issuedToken && (
					<HmacSecretReveal
						label="New SCIM token"
						value={issuedToken.token}
						onDismiss={onDismissToken}
					/>
				)}
				<dl className="space-y-3">
					<Row label="Status">
						<ProvisioningStatusBadge
							enabled={provider.is_enabled}
						/>
					</Row>
					<Row label="Tenant URL">
						<CopyField
							id="idp-tenant-url"
							label="Tenant URL"
							value={provider.scim_base_url}
						/>
					</Row>
					<Row label="Token">
						{provider.token ? (
							<span className="font-mono">
								••••{provider.token.hint}
							</span>
						) : (
							"None"
						)}
					</Row>
					<Row label="Expiration Date">
						<CredentialExpiryBadge token={provider.token} />
					</Row>
					<Row label="Last Request">
						{provider.last_request_at
							? formatDate(provider.last_request_at)
							: "Never"}
					</Row>
					<Row label="Users">
						<span className="tabular-nums">
							{provider.user_count}
						</span>
					</Row>
				</dl>
			</CardContent>
			<RotateTokenDialog
				provider={provider}
				open={rotateOpen}
				onOpenChange={setRotateOpen}
				onIssued={onIssued}
			/>
		</Card>
	);
}
