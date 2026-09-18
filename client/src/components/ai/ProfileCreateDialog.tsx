import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { MessageSquareText } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { ProviderModelField } from "./ProviderModelField";
import { FailoverProfileField } from "./FailoverProfileField";
import { providerLabel } from "./providerOptions";
import type { AIProviderConnection, AIModelProfile } from "@/services/aiModels";

export function ProfileCreateDialog({ profileCreateOpen, profileName, profileConnectionId, profileModel, profileChatEnabled, profileFailoverId, profileReady, providers, profiles, firstProfile, setProfileName, setProfileConnectionId, setProfileModel, setProfileChatEnabled, setProfileFailoverId, pending, error, onClose, onSubmit }: {
	profileCreateOpen: boolean;
	profileName: string;
	profileConnectionId: string;
	profileModel: string;
	profileChatEnabled: boolean;
	profileFailoverId: string | null;
	profileReady: boolean;
	providers: AIProviderConnection[];
	profiles: AIModelProfile[];
	firstProfile: boolean;
	setProfileName: (value: string) => void;
	setProfileConnectionId: (value: string) => void;
	setProfileModel: (value: string) => void;
	setProfileChatEnabled: (value: boolean) => void;
	setProfileFailoverId: (value: string | null) => void;
	pending: boolean;
	error: Error | null;
	onClose: () => void;
	onSubmit: () => void;
}) {
 const dialogFocus = useDialogReturnFocus();

 return (
			<Dialog
				open={profileCreateOpen}
				onOpenChange={(open) => { if (!open && !pending) onClose(); }}
			>
				<DialogContent {...dialogFocus} className="sm:max-w-[560px]">
					<DialogHeader>
						<DialogTitle>Add Model Profile</DialogTitle>
						<DialogDescription>
							Create a reusable provider and model combination.
						</DialogDescription>
					</DialogHeader>
					<fieldset disabled={pending} className="grid min-w-0 gap-4 py-2">
						<div className="space-y-2">
							<Label htmlFor="ai-profile-name">
								Profile Name
							</Label>
							<Input
								id="ai-profile-name"
								value={profileName}
								onChange={(event) =>
									setProfileName(event.target.value)
								}
								placeholder="Fast Support Chat"
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="ai-profile-provider">
								Provider Connection
							</Label>
							<Select
								disabled={pending}
								value={profileConnectionId}
								onValueChange={(connectionId) => {
									setProfileConnectionId(connectionId);
									setProfileModel("");
								}}
							>
								<SelectTrigger
									id="ai-profile-provider"
									className="h-auto min-h-11 w-full data-[size=default]:h-auto [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
								>
									<SelectValue placeholder="Select a provider connection" />
								</SelectTrigger>
								<SelectContent>
									{providers.map((provider) => (
										<SelectItem
											key={provider.id}
											value={provider.id}
										>
											{provider.name} ·{" "}
											{providerLabel(provider.provider)}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</div>
						<div>
							<ProviderModelField
								disabled={pending}
								id="ai-profile-model"
								connectionId={profileConnectionId}
								value={profileModel}
								onValueChange={setProfileModel}
							/>
						</div>
						<FailoverProfileField
							id="ai-profile-failover"
							profiles={profiles}
							currentProfileId={null}
							currentConnectionId={profileConnectionId}
							value={profileFailoverId}
							disabled={pending}
							onChange={setProfileFailoverId}
						/>
						<label className="flex items-center justify-between gap-4 rounded-lg border px-3 py-2.5 text-sm">
							<span>
								<span className="flex items-center gap-2">
									<MessageSquareText className="h-4 w-4 text-primary" />
									Enable for Chat
								</span>
								{firstProfile && (
									<span className="mt-1 block text-xs text-muted-foreground">
										Your first profile starts as the default
										for every assignment.
									</span>
								)}
							</span>
							<Switch
								size="sm"
								checked={profileChatEnabled}
								disabled={firstProfile || pending}
								onCheckedChange={setProfileChatEnabled}
							/>
						</label>
					</fieldset>
					{Boolean(error) && <p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">{error instanceof Error ? error.message : "Could not save. Check the fields and try again."} Your entries are preserved.</p>}
					<DialogFooter>
						<Button className="min-h-11"
							variant="outline"
							disabled={pending}
							onClick={onClose}
						>
							Cancel
						</Button>
						<Button className="min-h-11"
							disabled={
								!profileReady || pending
							}
							onClick={onSubmit}
						>
							{pending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Creating…
								</>
							) : (
								"Add Profile"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
 );
}
