import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

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
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getErrorMessage } from "@/lib/api-error";
import {
	VENDORS,
	useUpdateIdentityProvider,
	vendorLabel,
	type IdentityProvider,
	type IdentityProviderVendor,
} from "@/services/identity-providers";

function SwitchRow({
	id,
	label,
	description,
	checked,
	onCheckedChange,
}: {
	id: string;
	label: string;
	description: string;
	checked: boolean;
	onCheckedChange: (checked: boolean) => void;
}) {
	return (
		<div className="flex items-start gap-3">
			<Switch
				id={id}
				checked={checked}
				onCheckedChange={onCheckedChange}
			/>
			<div className="space-y-1">
				<Label htmlFor={id}>{label}</Label>
				<p className="text-sm text-muted-foreground">{description}</p>
			</div>
		</div>
	);
}

function EditContent({
	provider,
	onOpenChange,
}: {
	provider: IdentityProvider;
	onOpenChange: (open: boolean) => void;
}) {
	const update = useUpdateIdentityProvider();
	const [name, setName] = useState(provider.name);
	const [vendor, setVendor] = useState<IdentityProviderVendor>(
		provider.vendor,
	);
	const [enabled, setEnabled] = useState(provider.is_enabled);
	const [external, setExternal] = useState(provider.provision_as_external);
	const busy = update.isPending;
	const save = async () => {
		try {
			await update.mutateAsync({
				params: { path: { identity_provider_id: provider.id } },
				body: {
					name: name.trim(),
					vendor,
					is_enabled: enabled,
					provision_as_external: external,
				},
			});
			toast.success("Identity provider saved");
			onOpenChange(false);
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The connection could not be saved."),
			);
		}
	};
	return (
		<DialogContent className="sm:max-w-3xl">
			<DialogHeader>
				<DialogTitle>Configure Identity Provider</DialogTitle>
				<DialogDescription>
					Changes apply to the next request the identity provider
					sends.
				</DialogDescription>
			</DialogHeader>
			<div className="border-y py-4">
				<Tabs
					defaultValue="general"
					orientation="vertical"
					className="min-h-64 sm:flex-row"
				>
					<TabsList
						aria-label="Settings sections"
						className="h-auto w-full shrink-0 flex-col items-stretch sm:w-48"
					>
						<TabsTrigger value="general" className="justify-start">
							General
						</TabsTrigger>
						<TabsTrigger
							value="provisioning"
							className="justify-start"
						>
							Provisioning
						</TabsTrigger>
					</TabsList>
					<TabsContent
						value="general"
						className="min-w-0 flex-1 space-y-4"
					>
						<div className="space-y-2">
							<Label htmlFor="edit-idp-name">Display Name</Label>
							<Input
								id="edit-idp-name"
								value={name}
								onChange={(event) =>
									setName(event.target.value)
								}
								maxLength={255}
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="edit-idp-vendor">Provider</Label>
							<Select
								value={vendor}
								onValueChange={(value) =>
									setVendor(value as IdentityProviderVendor)
								}
							>
								<SelectTrigger
									id="edit-idp-vendor"
									className="w-full"
								>
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
					</TabsContent>
					<TabsContent
						value="provisioning"
						className="min-w-0 flex-1 space-y-5"
					>
						<SwitchRow
							id="edit-idp-enabled"
							label="Enable Provisioning"
							description="When off, the identity provider's requests are refused. Users and roles stay as they are."
							checked={enabled}
							onCheckedChange={setEnabled}
						/>
						<SwitchRow
							id="edit-idp-external"
							label="Provision as External Users"
							description="New users from this connection see only what a role or an explicit grant gives them. Users already provisioned keep their setting."
							checked={external}
							onCheckedChange={setExternal}
						/>
					</TabsContent>
				</Tabs>
			</div>
			<DialogFooter>
				<Button
					variant="outline"
					disabled={busy}
					onClick={() => onOpenChange(false)}
				>
					Cancel
				</Button>
				<Button
					disabled={busy || !name.trim()}
					onClick={() => void save()}
				>
					{busy && (
						<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
					)}
					Save
				</Button>
			</DialogFooter>
		</DialogContent>
	);
}

export function EditIdentityProviderDialog({
	provider,
	open,
	onOpenChange,
}: {
	provider: IdentityProvider;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<EditContent provider={provider} onOpenChange={onOpenChange} />
			)}
		</Dialog>
	);
}
