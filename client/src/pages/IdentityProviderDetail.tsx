import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { AlertCircle, ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DomainClaimsCard } from "@/components/identity-providers/DomainClaimsCard";
import { EditIdentityProviderDialog } from "@/components/identity-providers/EditIdentityProviderDialog";
import { GroupMappingCard } from "@/components/identity-providers/GroupMappingCard";
import { ProvisioningCard } from "@/components/identity-providers/ProvisioningCard";
import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getErrorMessage } from "@/lib/api-error";
import { orgTarget } from "@/lib/authorization";
import { formatDateShort } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";
import {
	IDENTITY_PROVIDERS_PATH,
	credentialStatus,
	useDeleteIdentityProvider,
	useIdentityProvider,
	vendorLabel,
	type IdentityProvider,
	type IdentityProviderTokenIssued,
} from "@/services/identity-providers";

/** What needs attention before provisioning can work, most urgent first. */
function Attention({ provider }: { provider: IdentityProvider }) {
	const status = credentialStatus(provider.token);
	if (status === "expired" || status === "none") {
		return (
			<Alert variant="destructive">
				<AlertCircle className="h-4 w-4" />
				<AlertTitle>
					{status === "none"
						? "This connection has no token"
						: "SCIM authentication token has expired"}
				</AlertTitle>
				<AlertDescription>
					Regenerate the token, copy it, then update and test the
					connection in the identity provider.
				</AlertDescription>
			</Alert>
		);
	}
	if (status === "expiring" && provider.token) {
		return (
			<Alert>
				<AlertCircle className="h-4 w-4" />
				<AlertTitle>
					SCIM token expires{" "}
					{formatDateShort(provider.token.expires_at)}
				</AlertTitle>
				<AlertDescription>
					Regenerate it and update the identity provider before then.
				</AlertDescription>
			</Alert>
		);
	}
	if (provider.verified_domains.length === 0) {
		return (
			<Alert>
				<AlertCircle className="h-4 w-4" />
				<AlertTitle>No verified email domains</AlertTitle>
				<AlertDescription>
					The identity provider can't create users until a domain is
					verified below.
				</AlertDescription>
			</Alert>
		);
	}
	return null;
}

export function IdentityProviderDetail() {
	const { identityProviderId = "" } = useParams();
	const navigate = useNavigate();
	const location = useLocation();
	const authorization = useAuthorization();
	const query = useIdentityProvider(identityProviderId);
	const remove = useDeleteIdentityProvider();
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [issuedToken, setIssuedToken] =
		useState<IdentityProviderTokenIssued | null>(
			(
				location.state as {
					issuedToken?: IdentityProviderTokenIssued;
				} | null
			)?.issuedToken ?? null,
		);
	const hasTokenInHistory = Boolean(
		(location.state as { issuedToken?: unknown } | null)?.issuedToken,
	);
	useEffect(() => {
		// The token now lives only in this page's state: take it out of the
		// history entry so a reload or back navigation can't show it again.
		if (hasTokenInHistory) {
			navigate(location.pathname, { replace: true, state: null });
		}
	}, [hasTokenInHistory, location.pathname, navigate]);
	const dismissToken = () => setIssuedToken(null);

	const provider = query.data;
	if (query.isLoading) {
		return (
			<div
				role="status"
				aria-label="Loading identity provider"
				className="mx-auto max-w-7xl space-y-4"
			>
				<Skeleton className="h-10 w-1/3" />
				<Skeleton className="h-48 w-full" />
			</div>
		);
	}
	if (!provider) {
		return (
			<Alert variant="destructive" className="mx-auto max-w-3xl">
				<AlertDescription>
					{query.error
						? getErrorMessage(
								query.error,
								"The identity provider could not be loaded.",
							)
						: "Identity provider not found."}
				</AlertDescription>
				<Button
					variant="outline"
					className="mt-3 w-fit"
					onClick={() => navigate(IDENTITY_PROVIDERS_PATH)}
				>
					<ArrowLeft className="mr-1 h-4 w-4" />
					Back to Identity Providers
				</Button>
			</Alert>
		);
	}

	const canWrite = authorization.canAt(
		"identityproviders.readwrite",
		orgTarget(provider.organization_id),
	);
	const onDelete = async () => {
		try {
			await remove.mutateAsync({
				params: { path: { identity_provider_id: provider.id } },
			});
			toast.success(`${provider.name} deleted`);
			navigate(IDENTITY_PROVIDERS_PATH);
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The connection could not be deleted."),
			);
		}
	};

	return (
		<PageWorkspace className="mx-auto max-w-7xl space-y-4">
			<div className="space-y-2">
				<Link
					to={IDENTITY_PROVIDERS_PATH}
					className="inline-flex min-h-11 items-center text-sm text-primary hover:underline"
				>
					<ArrowLeft className="mr-1 h-3.5 w-3.5" />
					Identity Providers
				</Link>
				<div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
					<div className="min-w-0">
						<h1 className="font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
							{provider.name}
						</h1>
						<p className="mt-1 text-sm text-muted-foreground">
							{vendorLabel(provider.vendor)} ·{" "}
							{provider.organization_name}
						</p>
					</div>
					{canWrite && (
						<div className="flex gap-2">
							<Button
								variant="outline"
								onClick={() => setEditOpen(true)}
							>
								<Pencil
									aria-hidden="true"
									className="mr-1.5 h-4 w-4"
								/>
								Edit
							</Button>
							<Button
								variant="outline"
								onClick={() => setDeleteOpen(true)}
							>
								<Trash2
									aria-hidden="true"
									className="mr-1.5 h-4 w-4"
								/>
								Delete
							</Button>
						</div>
					)}
				</div>
			</div>

			<Attention provider={provider} />
			<ProvisioningCard
				provider={provider}
				canWrite={canWrite}
				issuedToken={issuedToken}
				onDismissToken={dismissToken}
				onIssued={setIssuedToken}
			/>
			<DomainClaimsCard
				organizationId={provider.organization_id}
				organizationName={provider.organization_name}
				canWrite={canWrite}
			/>
			<GroupMappingCard provider={provider} canWrite={canWrite} />

			<EditIdentityProviderDialog
				provider={provider}
				open={editOpen}
				onOpenChange={setEditOpen}
			/>
			<AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete {provider.name}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							Its {provider.user_count} users keep their accounts
							and roles but become Native, and the identity
							provider can no longer provision them. Remove the
							app from the identity provider too.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={remove.isPending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={remove.isPending}
							onClick={(event) => {
								event.preventDefault();
								void onDelete();
							}}
						>
							Delete Connection
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</PageWorkspace>
	);
}
