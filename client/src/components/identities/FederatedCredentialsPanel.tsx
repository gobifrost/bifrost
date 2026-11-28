import { useState, type FormEvent } from "react";
import { Loader2, Plus, Trash2, X } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { getErrorMessage } from "@/lib/api-error";
import {
	useCreateFederatedCredential,
	useDeleteFederatedCredential,
	useFederatedCredentials,
	useUpdateFederatedCredential,
	type FederatedCredential,
} from "@/services/federated-credentials";

function CredentialForm({
	identityId,
	credential,
	onClose,
}: {
	identityId: string;
	credential?: FederatedCredential;
	onClose: () => void;
}) {
	const create = useCreateFederatedCredential();
	const update = useUpdateFederatedCredential();
	const [name, setName] = useState(credential?.name ?? "");
	const [issuer, setIssuer] = useState(credential?.issuer ?? "");
	const [discoveryUrl, setDiscoveryUrl] = useState(
		credential?.discovery_url ?? "",
	);
	const [audience, setAudience] = useState(
		credential?.audience ?? window.location.origin,
	);
	const [subject, setSubject] = useState(credential?.subject ?? "");
	const [tokenType, setTokenType] = useState(
		credential?.subject_token_type ??
			"urn:ietf:params:oauth:token-type:id_token",
	);
	const [ttl, setTtl] = useState(credential?.token_ttl_seconds ?? 300);
	const [claims, setClaims] = useState(() =>
		Object.entries(credential?.claims ?? {}).map(([key, value]) => ({
			key,
			value,
		})),
	);
	const [error, setError] = useState<string | null>(null);
	const busy = create.isPending || update.isPending;
	async function save(event: FormEvent) {
		event.preventDefault();
		if (busy) return;
		setError(null);
		const keys = claims.map((claim) => claim.key.trim());
		if (new Set(keys).size !== keys.length) {
			setError("Each claim name must be unique.");
			return;
		}
		const body = {
			name: name.trim(),
			enabled: credential?.enabled ?? true,
			issuer,
			discovery_url: discoveryUrl || null,
			audience,
			subject,
			subject_token_type: tokenType,
			token_ttl_seconds: ttl,
			claims: Object.fromEntries(
				claims.map((claim) => [claim.key.trim(), claim.value]),
			),
		};
		try {
			if (credential)
				await update.mutateAsync({
					params: {
						path: {
							identity_id: identityId,
							credential_id: credential.id,
						},
					},
					body,
				});
			else
				await create.mutateAsync({
					params: { path: { identity_id: identityId } },
					body,
				});
			onClose();
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The credential could not be saved."),
			);
		}
	}
	return (
		<DialogContent
			className="sm:max-w-xl"
			showCloseButton={!busy}
			onEscapeKeyDown={(event) => {
				if (busy) event.preventDefault();
			}}
			onInteractOutside={(event) => {
				if (busy) event.preventDefault();
			}}
		>
			<form onSubmit={save} className="space-y-4">
				<DialogHeader>
					<DialogTitle>
						{credential
							? "Edit Federated Credential"
							: "New Federated Credential"}
					</DialogTitle>
					<DialogDescription>
						Allow an external workload to sign in as this identity.
						Its roles determine what the workload can do.
					</DialogDescription>
				</DialogHeader>
				<div className="max-h-[60dvh] space-y-4 overflow-y-auto px-1">
					{error && (
						<Alert variant="destructive">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<div className="space-y-2">
						<Label htmlFor="federation-name">Name</Label>
						<Input
							id="federation-name"
							value={name}
							onChange={(event) => setName(event.target.value)}
							required
							autoFocus
							disabled={busy}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="federation-issuer">Issuer URL</Label>
						<Input
							id="federation-issuer"
							type="url"
							placeholder="https://token.actions.githubusercontent.com"
							value={issuer}
							onChange={(event) => setIssuer(event.target.value)}
							required
							disabled={busy}
						/>
						<p className="text-sm text-muted-foreground">
							The HTTPS issuer published by your identity
							provider.
						</p>
					</div>
					<div className="space-y-2">
						<Label htmlFor="federation-type">
							External Token Type
						</Label>
						<Select
							value={tokenType}
							onValueChange={setTokenType}
							disabled={busy}
						>
							<SelectTrigger id="federation-type">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="urn:ietf:params:oauth:token-type:id_token">
									ID Token (Including GitHub Actions)
								</SelectItem>
								<SelectItem value="urn:ietf:params:oauth:token-type:access_token">
									Access Token (Including Azure Managed
									Identity)
								</SelectItem>
							</SelectContent>
						</Select>
					</div>
					<div className="space-y-2">
						<Label htmlFor="federation-audience">Audience</Label>
						<Input
							id="federation-audience"
							value={audience}
							onChange={(event) =>
								setAudience(event.target.value)
							}
							required
							disabled={busy}
						/>
						<p className="text-sm text-muted-foreground">
							Use a value dedicated to this Bifrost instance.
							Request that same audience from your provider.
						</p>
					</div>
					<div className="space-y-2">
						<Label htmlFor="federation-subject">Subject</Label>
						<Input
							id="federation-subject"
							value={subject}
							onChange={(event) => setSubject(event.target.value)}
							required
							disabled={busy}
						/>
						<p className="text-sm text-muted-foreground">
							Copy the exact subject from your provider's
							configuration. Wildcards are not supported.
						</p>
					</div>
					<details className="space-y-2">
						<summary className="cursor-pointer text-sm font-medium">
							Discovery Settings
						</summary>
						<Label htmlFor="federation-discovery">
							Discovery URL (Optional)
						</Label>
						<Input
							id="federation-discovery"
							type="url"
							value={discoveryUrl}
							onChange={(event) =>
								setDiscoveryUrl(event.target.value)
							}
							disabled={busy}
						/>
						<p className="text-sm text-muted-foreground">
							Leave blank to discover from the issuer. Set your
							provider's published discovery URL when it uses a
							different address, such as Microsoft Entra v1.
						</p>
					</details>
					<fieldset className="space-y-3">
						<legend className="text-sm font-medium">
							Additional Claim Restrictions
						</legend>
						<p className="text-sm text-muted-foreground">
							Every listed claim must match exactly, such as a
							repository ID, branch, environment, or tenant ID.
						</p>
						{claims.map((claim, index) => (
							<div key={index} className="flex items-end gap-2">
								<div className="min-w-0 flex-1 space-y-2">
									<Label htmlFor={`claim-name-${index}`}>
										Claim Name {index + 1}
									</Label>
									<Input
										id={`claim-name-${index}`}
										value={claim.key}
										onChange={(event) =>
											setClaims(
												claims.map((item, i) =>
													i === index
														? {
																...item,
																key: event
																	.target
																	.value,
															}
														: item,
												),
											)
										}
										required
										disabled={busy}
									/>
								</div>
								<div className="min-w-0 flex-1 space-y-2">
									<Label htmlFor={`claim-value-${index}`}>
										Exact Value {index + 1}
									</Label>
									<Input
										id={`claim-value-${index}`}
										value={claim.value}
										onChange={(event) =>
											setClaims(
												claims.map((item, i) =>
													i === index
														? {
																...item,
																value: event
																	.target
																	.value,
															}
														: item,
												),
											)
										}
										required
										disabled={busy}
									/>
								</div>
								<Button
									type="button"
									variant="ghost"
									size="icon"
									className="min-h-11 min-w-11 sm:min-h-9 sm:min-w-9"
									aria-label={`Remove claim ${index + 1}`}
									disabled={busy}
									onClick={() =>
										setClaims(
											claims.filter(
												(_, i) => i !== index,
											),
										)
									}
								>
									<X aria-hidden="true" className="size-4" />
								</Button>
							</div>
						))}
						<Button
							type="button"
							variant="outline"
							disabled={busy}
							onClick={() =>
								setClaims([...claims, { key: "", value: "" }])
							}
						>
							<Plus aria-hidden="true" className="size-4" />
							Add Claim
						</Button>
					</fieldset>
					<div className="space-y-2">
						<Label htmlFor="federation-ttl">
							Access Token Lifetime (Seconds)
						</Label>
						<Input
							id="federation-ttl"
							type="number"
							min={60}
							max={3600}
							value={ttl}
							onChange={(event) =>
								setTtl(Number(event.target.value))
							}
							required
							disabled={busy}
						/>
						<p className="text-sm text-muted-foreground">
							Tokens expire without a refresh token. The workload
							signs in again when needed.
						</p>
					</div>
					{credential && (
						<p className="text-sm text-muted-foreground">
							Saving changes revokes tokens already issued through
							this credential.
						</p>
					)}
				</div>
				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						disabled={busy}
						onClick={onClose}
					>
						Cancel
					</Button>
					<Button type="submit" disabled={busy}>
						{busy && (
							<Loader2
								className="size-4 animate-spin motion-reduce:animate-none"
								aria-hidden="true"
							/>
						)}
						Save Credential
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function FederatedCredentialsPanel({
	identityId,
	canManage,
}: {
	identityId: string;
	canManage: boolean;
}) {
	const query = useFederatedCredentials(identityId);
	const update = useUpdateFederatedCredential();
	const remove = useDeleteFederatedCredential();
	const [editing, setEditing] = useState<FederatedCredential | "new" | null>(
		null,
	);
	const [deleting, setDeleting] = useState<FederatedCredential | null>(null);
	const [error, setError] = useState<string | null>(null);
	const busy = update.isPending || remove.isPending;
	async function toggle(credential: FederatedCredential) {
		setError(null);
		try {
			await update.mutateAsync({
				params: {
					path: {
						identity_id: identityId,
						credential_id: credential.id,
					},
				},
				body: { enabled: !credential.enabled },
			});
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The credential could not be updated."),
			);
		}
	}
	async function deleteCredential() {
		if (!deleting || busy) return;
		setError(null);
		try {
			await remove.mutateAsync({
				params: {
					path: {
						identity_id: identityId,
						credential_id: deleting.id,
					},
				},
			});
			setDeleting(null);
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The credential could not be deleted."),
			);
		}
	}
	return (
		<section
			aria-labelledby="federated-credentials-heading"
			className="space-y-3"
		>
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="space-y-1">
					<h2
						id="federated-credentials-heading"
						className="font-display text-lg font-semibold"
					>
						Federated Credentials
					</h2>
					<p className="text-sm text-muted-foreground">
						Let trusted workloads sign in as this identity without a
						stored Bifrost secret.
					</p>
				</div>
				{canManage && (
					<Button
						variant="outline"
						onClick={() => {
							setError(null);
							setEditing("new");
						}}
					>
						<Plus aria-hidden="true" className="size-4" />
						New Federated Credential
					</Button>
				)}
			</div>
			{error && !deleting && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			{query.isLoading ? (
				<Skeleton
					className="h-24 w-full"
					aria-label="Loading federated credentials"
					role="status"
				/>
			) : query.isError ? (
				<Alert variant="destructive">
					<AlertDescription>
						Federated credentials could not be loaded.{" "}
						<Button
							variant="link"
							disabled={query.isFetching}
							onClick={() => void query.refetch()}
						>
							Try Again
						</Button>
					</AlertDescription>
				</Alert>
			) : query.data?.length ? (
				<ul className="divide-y rounded-sm border bg-card">
					{query.data.map((credential) => (
						<li key={credential.id} className="space-y-3 p-4">
							<div className="flex flex-wrap items-center justify-between gap-3">
								<div className="flex min-w-0 flex-wrap items-center gap-2">
									<span className="font-medium [overflow-wrap:anywhere]">
										{credential.name}
									</span>
									<Badge variant="outline">
										{credential.enabled
											? "Enabled"
											: "Disabled"}
									</Badge>
								</div>
								{canManage && (
									<div className="flex flex-wrap gap-2">
										<Button
											size="sm"
											className="min-h-11 sm:min-h-8"
											variant="outline"
											disabled={busy}
											onClick={() =>
												setEditing(credential)
											}
											aria-label={`Edit ${credential.name}`}
										>
											Edit
										</Button>
										<Button
											size="sm"
											className="min-h-11 sm:min-h-8"
											variant="outline"
											disabled={busy}
											onClick={() =>
												void toggle(credential)
											}
											aria-label={`${credential.enabled ? "Disable" : "Enable"} ${credential.name}`}
										>
											{credential.enabled
												? "Disable"
												: "Enable"}
										</Button>
										<Button
											size="icon"
											className="min-h-11 min-w-11 sm:min-h-9 sm:min-w-9"
											variant="ghost"
											disabled={busy}
											aria-label={`Delete ${credential.name}`}
											onClick={() => {
												setError(null);
												setDeleting(credential);
											}}
										>
											<Trash2
												className="size-4"
												aria-hidden="true"
											/>
										</Button>
									</div>
								)}
							</div>
							<dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[7rem_minmax(0,1fr)]">
								<dt className="text-muted-foreground">
									Issuer
								</dt>
								<dd className="[overflow-wrap:anywhere]">
									{credential.issuer}
								</dd>
								<dt className="text-muted-foreground">
									Audience
								</dt>
								<dd className="[overflow-wrap:anywhere]">
									{credential.audience}
								</dd>
								<dt className="text-muted-foreground">
									Subject
								</dt>
								<dd className="[overflow-wrap:anywhere]">
									{credential.subject}
								</dd>
								{Object.entries(credential.claims ?? {}).map(
									([key, value]) => (
										<div key={key} className="contents">
											<dt className="text-muted-foreground [overflow-wrap:anywhere]">
												{key}
											</dt>
											<dd className="[overflow-wrap:anywhere]">
												{value}
											</dd>
										</div>
									),
								)}
								<dt className="text-muted-foreground">
									Token Lifetime
								</dt>
								<dd>{credential.token_ttl_seconds} seconds</dd>
							</dl>
						</li>
					))}
				</ul>
			) : (
				<p className="rounded-sm border border-dashed p-5 text-sm text-muted-foreground">
					No federated credentials. Workloads cannot sign in as this
					identity.
				</p>
			)}
			<Dialog
				open={editing !== null}
				onOpenChange={(open) => {
					if (!open) setEditing(null);
				}}
			>
				{editing && (
					<CredentialForm
						key={editing === "new" ? "new" : editing.id}
						identityId={identityId}
						credential={editing === "new" ? undefined : editing}
						onClose={() => setEditing(null)}
					/>
				)}
			</Dialog>
			<Dialog
				open={deleting !== null}
				onOpenChange={(open) => {
					if (!open && !busy) setDeleting(null);
				}}
			>
				<DialogContent
					showCloseButton={!busy}
					onEscapeKeyDown={(event) => {
						if (busy) event.preventDefault();
					}}
					onInteractOutside={(event) => {
						if (busy) event.preventDefault();
					}}
				>
					<DialogHeader>
						<DialogTitle>Delete Federated Credential</DialogTitle>
						<DialogDescription>
							Delete {deleting?.name}? New sign-ins will be denied
							and tokens issued through this credential will be
							revoked.
						</DialogDescription>
					</DialogHeader>
					{error && (
						<Alert variant="destructive">
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<DialogFooter>
						<Button
							variant="outline"
							disabled={busy}
							onClick={() => setDeleting(null)}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							disabled={busy}
							onClick={() => void deleteCredential()}
						>
							{busy && (
								<Loader2
									aria-hidden="true"
									className="size-4 animate-spin motion-reduce:animate-none"
								/>
							)}
							Delete Credential
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</section>
	);
}
