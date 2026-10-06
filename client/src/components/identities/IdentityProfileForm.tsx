import { useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getErrorMessage } from "@/lib/api-error";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";
import { useAuthorization } from "@/services/authorization";
import { useRenameIdentity } from "@/services/identities";

type User = components["schemas"]["UserPublic"];

/**
 * An identity's profile is its name: identities have no email, sign-in or
 * status to edit. Renaming is users.lifecycle.readwrite at its organization.
 */
export function IdentityProfileForm({ identity }: { identity: User }) {
	const authorization = useAuthorization();
	const rename = useRenameIdentity();
	const [name, setName] = useState(identity.name || "");
	const [error, setError] = useState<string | null>(null);
	const canRename =
		authorization.canAt(
			"users.lifecycle.readwrite",
			orgTarget(identity.organization_id),
		) && !(identity.is_protected && !authorization.isPlatformAdmin);
	const trimmed = name.trim();
	const dirty = trimmed !== (identity.name || "");

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!canRename || !dirty || rename.isPending) return;
		if (!trimmed) {
			setError("Please enter a name");
			return;
		}
		setError(null);
		try {
			await rename.mutateAsync({
				params: { path: { identity_id: identity.id } },
				body: { name: trimmed },
			});
			toast.success("Identity renamed", {
				description: `It's now named ${trimmed}`,
			});
		} catch (cause) {
			setError(
				getErrorMessage(cause, "The identity could not be renamed."),
			);
		}
	};

	return (
		<form onSubmit={handleSubmit} className="max-w-2xl space-y-6">
			{error && (
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			<div className="space-y-2">
				<Label htmlFor="identity-profile-name">Name</Label>
				<Input
					id="identity-profile-name"
					value={name}
					onChange={(event) => setName(event.target.value)}
					disabled={!canRename}
					maxLength={255}
					aria-describedby={
						canRename ? undefined : "identity-profile-name-hint"
					}
				/>
				{!canRename && (
					<p
						id="identity-profile-name-hint"
						className="text-xs text-muted-foreground"
					>
						Only people who can create, move, or delete users in
						this organization can rename it.
					</p>
				)}
			</div>
			{canRename && (
				<div className="flex justify-end">
					<Button
						type="submit"
						className="h-11"
						disabled={!dirty || rename.isPending}
					>
						{rename.isPending && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						Save Name
					</Button>
				</div>
			)}
		</form>
	);
}
