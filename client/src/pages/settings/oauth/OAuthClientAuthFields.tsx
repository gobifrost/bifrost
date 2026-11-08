import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export type TokenEndpointAuthMethod =
	| "client_secret_post"
	| "client_secret_basic"
	| "private_key_jwt";

export interface OAuthClientAuthDraft {
	token_endpoint_auth_method: TokenEndpointAuthMethod;
	private_key: string;
	private_key_kid: string;
	private_key_alg: string;
	clear_private_key: boolean;
	scopes: string;
}

interface OAuthClientAuthFieldsProps extends OAuthClientAuthDraft {
	idPrefix: string;
	privateKeySet: boolean;
	onChange: (patch: Partial<OAuthClientAuthDraft>) => void;
}

const METHODS: { value: TokenEndpointAuthMethod; label: string }[] = [
	{ value: "client_secret_post", label: "Client secret (POST body)" },
	{ value: "client_secret_basic", label: "Client secret (HTTP Basic)" },
	{ value: "private_key_jwt", label: "Private key JWT" },
];

const ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"];

export function OAuthClientAuthFields({
	idPrefix,
	token_endpoint_auth_method,
	private_key,
	private_key_kid,
	private_key_alg,
	clear_private_key,
	scopes,
	privateKeySet,
	onChange,
}: OAuthClientAuthFieldsProps) {
	const usesPrivateKey = token_endpoint_auth_method === "private_key_jwt";
	// The backend only writes a kid when the key is replaced, so the field is
	// editable only when new key material is being supplied.
	const canEditKid =
		!privateKeySet || Boolean(private_key) || clear_private_key;

	return (
		<div className="space-y-4">
			<div className="space-y-2">
				<Label htmlFor={`${idPrefix}-auth-method`}>
					Client authentication
				</Label>
				<Select
					value={token_endpoint_auth_method}
					onValueChange={(value) =>
						onChange({
							token_endpoint_auth_method:
								value as TokenEndpointAuthMethod,
						})
					}
				>
					<SelectTrigger id={`${idPrefix}-auth-method`}>
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						{METHODS.map((method) => (
							<SelectItem key={method.value} value={method.value}>
								{method.label}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
			</div>

			{usesPrivateKey ? (
				<>
					<div className="space-y-2">
						<Label htmlFor={`${idPrefix}-private-key`}>
							Private key (PEM)
						</Label>
						<Textarea
							id={`${idPrefix}-private-key`}
							className="min-h-24 font-mono text-xs"
							placeholder={
								privateKeySet
									? "A key is stored. Paste a new PEM to replace it."
									: "Paste an RSA or EC private key in PEM format"
							}
							value={private_key}
							disabled={clear_private_key}
							onChange={(event) =>
								onChange({ private_key: event.target.value })
							}
						/>
						<p className="text-xs text-muted-foreground">
							{privateKeySet
								? "Write-only. The stored key is never returned."
								: "Write-only. Encrypted at rest."}
						</p>
					</div>
					<div className="grid gap-4 sm:grid-cols-2">
						<div className="space-y-2">
							<Label htmlFor={`${idPrefix}-private-key-kid`}>
								Key ID (kid)
							</Label>
							<Input
								id={`${idPrefix}-private-key-kid`}
								placeholder="optional"
								value={private_key_kid}
								disabled={!canEditKid}
								onChange={(event) =>
									onChange({
										private_key_kid: event.target.value,
									})
								}
							/>
							{!canEditKid ? (
								<p className="text-xs text-muted-foreground">
									Supply a new key to change the key ID.
								</p>
							) : null}
						</div>
						<div className="space-y-2">
							<Label htmlFor={`${idPrefix}-private-key-alg`}>
								Algorithm
							</Label>
							<Select
								value={private_key_alg}
								onValueChange={(value) =>
									onChange({ private_key_alg: value })
								}
							>
								<SelectTrigger id={`${idPrefix}-private-key-alg`}>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{ALGORITHMS.map((algorithm) => (
										<SelectItem
											key={algorithm}
											value={algorithm}
										>
											{algorithm}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</div>
					</div>
				</>
			) : (
				<p className="text-xs text-muted-foreground">
					A client secret is required for this method.
				</p>
			)}

			{privateKeySet || usesPrivateKey ? (
				<div className="space-y-1">
					<div className="flex items-center gap-2">
						<Checkbox
							id={`${idPrefix}-clear-private-key`}
							checked={clear_private_key}
							onCheckedChange={(checked) =>
								onChange({
									clear_private_key: checked === true,
									private_key: "",
									private_key_kid: "",
								})
							}
						/>
						<Label
							htmlFor={`${idPrefix}-clear-private-key`}
							className="font-normal"
						>
							Remove the stored private key
						</Label>
					</div>
					{clear_private_key && usesPrivateKey ? (
						<p className="text-xs text-muted-foreground">
							Choose a client-secret method to revoke the key and
							save.
						</p>
					) : null}
				</div>
			) : null}

			<div className="space-y-2">
				<Label htmlFor={`${idPrefix}-scopes`}>Scopes</Label>
				<Input
					id={`${idPrefix}-scopes`}
					placeholder="openid email profile"
					value={scopes}
					onChange={(event) => onChange({ scopes: event.target.value })}
				/>
				<p className="text-xs text-muted-foreground">
					Space or comma separated. Leave blank to use the provider
					default; openid is required.
				</p>
			</div>
		</div>
	);
}
