import { useState } from "react";
import { CheckCircle2, Clock, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { CopyField } from "@/components/identity-providers/CopyField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { getErrorMessage } from "@/lib/api-error";
import {
	useAddOrganizationDomain,
	useDeleteOrganizationDomain,
	useOrganizationDomains,
	useVerifyOrganizationDomain,
	type OrganizationDomain,
} from "@/services/identity-providers";

function ClaimRow({
	claim,
	canWrite,
}: {
	claim: OrganizationDomain;
	canWrite: boolean;
}) {
	const verify = useVerifyOrganizationDomain();
	const remove = useDeleteOrganizationDomain();
	const verified = claim.verified_at !== null;
	const onVerify = async () => {
		try {
			const result = await verify.mutateAsync({
				params: { path: { claim_id: claim.id } },
			});
			if (result.verified_at) {
				toast.success(`${claim.domain} verified`);
			} else {
				toast.error(
					`The TXT record for ${claim.domain} wasn't found yet`,
					{
						description:
							"DNS changes can take a while to propagate. Try again shortly.",
					},
				);
			}
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The domain could not be verified."),
			);
		}
	};
	const onRemove = async () => {
		try {
			await remove.mutateAsync({
				params: { path: { claim_id: claim.id } },
			});
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The domain could not be removed."),
			);
		}
	};
	return (
		<li className="space-y-3 py-3">
			<div className="flex flex-wrap items-center gap-2">
				<span className="font-medium [overflow-wrap:anywhere]">
					@{claim.domain}
				</span>
				{verified ? (
					<Badge variant="success">
						<CheckCircle2
							aria-hidden="true"
							className="h-3.5 w-3.5"
						/>
						Verified
					</Badge>
				) : (
					<Badge variant="warning">
						<Clock aria-hidden="true" className="h-3.5 w-3.5" />
						Pending
					</Badge>
				)}
				{canWrite && (
					<div className="ml-auto flex gap-2">
						{!verified && (
							<Button
								size="sm"
								variant="outline"
								disabled={verify.isPending}
								onClick={() => void onVerify()}
							>
								{verify.isPending && (
									<Loader2 className="mr-1.5 h-3.5 w-3.5 motion-safe:animate-spin" />
								)}
								Verify
							</Button>
						)}
						<Button
							size="sm"
							variant="ghost"
							aria-label={`Remove ${claim.domain}`}
							disabled={remove.isPending}
							onClick={() => void onRemove()}
						>
							<Trash2 aria-hidden="true" className="h-4 w-4" />
						</Button>
					</div>
				)}
			</div>
			{!verified && (
				<div className="space-y-2 text-sm">
					<p className="text-muted-foreground">
						Add a TXT record on{" "}
						<strong>{claim.txt_record_name}</strong> with this
						value, then select Verify.
					</p>
					<CopyField
						id={`txt-${claim.id}`}
						label={`TXT record for ${claim.domain}`}
						value={claim.txt_record_value}
					/>
				</div>
			)}
		</li>
	);
}

/**
 * The organization's email domains. A connection only provisions users on
 * verified domains, and each domain is verified by one organization.
 */
export function DomainClaimsCard({
	organizationId,
	organizationName,
	canWrite,
}: {
	organizationId: string;
	organizationName: string;
	canWrite: boolean;
}) {
	const query = useOrganizationDomains(organizationId);
	const add = useAddOrganizationDomain();
	const [domain, setDomain] = useState("");
	const onAdd = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!domain.trim()) return;
		try {
			await add.mutateAsync({
				body: {
					organization_id: organizationId,
					domain: domain.trim(),
				},
			});
			setDomain("");
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The domain could not be added."),
			);
		}
	};
	const claims = query.data ?? [];
	return (
		<Card>
			<CardHeader>
				<CardTitle>Email Domains</CardTitle>
				<CardDescription>
					{organizationName}'s domains. Users are only provisioned on
					verified domains; they apply to every connection in the
					organization.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-3">
				{query.isLoading ? (
					<Skeleton className="h-10 w-full" />
				) : claims.length === 0 ? (
					<p className="text-sm text-muted-foreground">
						No domains yet. Add one and verify it before the
						identity provider sends users.
					</p>
				) : (
					<ul aria-label="Email domains" className="divide-y">
						{claims.map((claim) => (
							<ClaimRow
								key={claim.id}
								claim={claim}
								canWrite={canWrite}
							/>
						))}
					</ul>
				)}
				{canWrite && (
					<form
						onSubmit={onAdd}
						className="flex flex-wrap items-end gap-2"
					>
						<div className="min-w-0 flex-1 space-y-1">
							<Label htmlFor="new-domain">Add Domain</Label>
							<Input
								id="new-domain"
								value={domain}
								onChange={(event) =>
									setDomain(event.target.value)
								}
								placeholder="contoso.com"
								maxLength={253}
							/>
						</div>
						<Button
							type="submit"
							variant="outline"
							disabled={!domain.trim() || add.isPending}
						>
							<Plus
								aria-hidden="true"
								className="mr-1.5 h-4 w-4"
							/>
							Add Domain
						</Button>
					</form>
				)}
			</CardContent>
		</Card>
	);
}
