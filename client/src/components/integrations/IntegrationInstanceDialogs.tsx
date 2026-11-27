import { useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { IntegrationInstanceOption } from "./IntegrationInstanceSelector";

export type InstanceAuthenticationMethod = "config" | "oauth";

export interface CreateIntegrationInstanceInput {
	name: string;
	key: string;
	authentication_method: InstanceAuthenticationMethod;
}

function suggestedKey(name: string) {
	return name
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.slice(0, 64);
}

export function AddIntegrationInstanceDialog({
	open,
	onOpenChange,
	onCreate,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreate: (input: CreateIntegrationInstanceInput) => Promise<void>;
}) {
	const [name, setName] = useState("");
	const [key, setKey] = useState("");
	const [keyEdited, setKeyEdited] = useState(false);
	const [authenticationMethod, setAuthenticationMethod] =
		useState<InstanceAuthenticationMethod>("config");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const reset = () => {
		setName("");
		setKey("");
		setKeyEdited(false);
		setAuthenticationMethod("config");
		setError(null);
	};
	const changeOpen = (nextOpen: boolean) => {
		if (saving) return;
		if (!nextOpen) reset();
		onOpenChange(nextOpen);
	};

	const handleNameChange = (nextName: string) => {
		setName(nextName);
		if (!keyEdited) setKey(suggestedKey(nextName));
	};

	const submit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (saving) return;
		const trimmedName = name.trim();
		const normalizedKey = suggestedKey(key);
		if (!trimmedName || !normalizedKey) {
			setError("Enter an instance name and key.");
			return;
		}
		setSaving(true);
		setError(null);
		try {
			await onCreate({
				name: trimmedName,
				key: normalizedKey,
				authentication_method: authenticationMethod,
			});
			reset();
			onOpenChange(false);
		} catch {
			setError("Could not add this instance. Review the values and try again.");
		} finally {
			setSaving(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={changeOpen}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Add Instance</DialogTitle>
					<DialogDescription>
						Create a separate connection for this integration. Credentials,
						defaults, and organization mappings start empty.
					</DialogDescription>
				</DialogHeader>
				<form className="space-y-5" onSubmit={(event) => void submit(event)}>
					<div className="space-y-2">
						<Label htmlFor="instance-name">Instance Name</Label>
						<Input
							id="instance-name"
							value={name}
							onChange={(event) => handleNameChange(event.target.value)}
							autoComplete="off"
							disabled={saving}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="instance-key">Instance Key</Label>
						<Input
							id="instance-key"
							value={key}
							onChange={(event) => {
								setKeyEdited(true);
								setKey(event.target.value);
							}}
							autoComplete="off"
							disabled={saving}
						/>
						<p className="text-xs text-muted-foreground">
							A stable identifier used by workflows and SDK calls. It cannot be
							changed later.
						</p>
					</div>
					<fieldset disabled={saving} className="space-y-2">
						<legend className="text-sm font-medium">Authentication</legend>
						<RadioGroup
							value={authenticationMethod}
							onValueChange={(value) =>
								setAuthenticationMethod(value as InstanceAuthenticationMethod)
							}
							className="gap-3"
						>
							<Label htmlFor="instance-auth-config" className="items-start rounded-[var(--bf-radius-surface)] border border-border/70 p-3">
								<RadioGroupItem
									id="instance-auth-config"
									value="config"
								/>
								<span className="space-y-0.5">
									<span className="block">API Key / Custom Credentials</span>
									<span className="block text-xs font-normal text-muted-foreground">
										Configure values for this instance after it is created.
									</span>
								</span>
							</Label>
							<Label htmlFor="instance-auth-oauth" className="items-start rounded-[var(--bf-radius-surface)] border border-border/70 p-3">
								<RadioGroupItem
									id="instance-auth-oauth"
									value="oauth"
								/>
								<span className="space-y-0.5">
									<span className="block">OAuth</span>
									<span className="block text-xs font-normal text-muted-foreground">
										Set up OAuth settings and connect after the instance is created.
									</span>
								</span>
							</Label>
						</RadioGroup>
					</fieldset>
					{error && <p role="alert" className="text-sm text-destructive">{error}</p>}
					<DialogFooter>
						<Button type="button" variant="outline" onClick={() => changeOpen(false)} disabled={saving}>
							Cancel
						</Button>
						<Button type="submit" disabled={saving}>
							{saving && <Loader2 className="size-4 motion-safe:animate-spin" />}
							{saving ? "Adding…" : "Add Instance"}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

export function ManageIntegrationInstancesDialog({
	open,
	onOpenChange,
	instances,
	onRename,
	onDelete,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	instances: IntegrationInstanceOption[];
	onRename: (instanceId: string, name: string) => Promise<void>;
	onDelete: (instanceId: string) => Promise<void>;
}) {
	const [names, setNames] = useState<Record<string, string>>(() =>
		Object.fromEntries(instances.map((instance) => [instance.id, instance.name])),
	);
	const [savingId, setSavingId] = useState<string | null>(null);
	const [deleting, setDeleting] = useState<IntegrationInstanceOption | null>(null);
	const [deletePending, setDeletePending] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const changeOpen = (nextOpen: boolean) => {
		if (savingId || deletePending) return;
		if (!nextOpen) {
			setNames(
				Object.fromEntries(instances.map((instance) => [instance.id, instance.name])),
			);
			setError(null);
		}
		onOpenChange(nextOpen);
	};

	const rename = async (instance: IntegrationInstanceOption) => {
		const nextName = names[instance.id]?.trim();
		if (!nextName || nextName === instance.name || savingId) return;
		setSavingId(instance.id);
		setError(null);
		try {
			await onRename(instance.id, nextName);
		} catch {
			setError(`Could not rename ${instance.name}. Try again.`);
		} finally {
			setSavingId(null);
		}
	};

	const confirmDelete = async () => {
		if (!deleting || deletePending) return;
		setDeletePending(true);
		setError(null);
		try {
			await onDelete(deleting.id);
			setDeleting(null);
		} catch {
			setError(`Could not delete ${deleting.name}. Try again.`);
		} finally {
			setDeletePending(false);
		}
	};

	return (
		<>
			<Dialog open={open} onOpenChange={changeOpen}>
				<DialogContent className="max-w-xl">
					<DialogHeader>
						<DialogTitle>Manage Instances</DialogTitle>
						<DialogDescription>
							Rename connection instances or remove a named instance. The Default
							instance is fixed.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-3">
						{instances.map((instance) => (
							<div key={instance.id} className="rounded-[var(--bf-radius-surface)] border border-border/70 p-3">
								<div className="flex flex-col gap-3 sm:flex-row sm:items-end">
									<div className="min-w-0 flex-1 space-y-2">
										<Label htmlFor={`instance-name-${instance.id}`}>
											{instance.is_default ? "Default Instance" : "Instance Name"}
										</Label>
										<Input
											id={`instance-name-${instance.id}`}
											aria-label={`Instance name: ${instance.name}`}
											value={names[instance.id] ?? instance.name}
											disabled={instance.is_default || savingId === instance.id}
											onChange={(event) => setNames((current) => ({ ...current, [instance.id]: event.target.value }))}
										/>
										<p className="text-xs text-muted-foreground">Key: {instance.key}</p>
									</div>
									{instance.is_default ? (
										<p className="pb-2 text-sm text-muted-foreground">Fixed default</p>
									) : (
										<div className="flex gap-2">
											<Button type="button" variant="outline" onClick={() => void rename(instance)} disabled={savingId === instance.id} aria-label={`Save ${instance.name}`}>
												{savingId === instance.id ? "Saving…" : "Save"}
											</Button>
											<Button type="button" variant="outline" className="text-destructive" onClick={() => setDeleting(instance)} aria-label={`Delete ${instance.name}`}>
												<Trash2 className="size-4" />
												Delete
											</Button>
										</div>
									)}
								</div>
							</div>
						))}
					</div>
					{error && <p role="alert" className="text-sm text-destructive">{error}</p>}
					<DialogFooter>
						<Button type="button" variant="outline" onClick={() => changeOpen(false)} disabled={Boolean(savingId)}>
							Done
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
			<AlertDialog open={Boolean(deleting)} onOpenChange={(next) => !next && !deletePending && setDeleting(null)}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete {deleting?.name}?</AlertDialogTitle>
						<AlertDialogDescription>
							This permanently deletes this instance’s credentials, defaults, and organization mappings. This cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deletePending}>Cancel</AlertDialogCancel>
						<AlertDialogAction variant="destructive" disabled={deletePending} onClick={(event) => { event.preventDefault(); void confirmDelete(); }}>
							{deletePending ? "Deleting…" : "Delete Instance"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
