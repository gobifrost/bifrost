import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ProviderModelField } from "./ProviderModelField";
import { FailoverProfileField } from "./FailoverProfileField";
import type { AIProviderConnection, AIProviderKind, AIModelProfile } from "@/services/aiModels";

export interface ModelProfileEditDraft {
	id: string;
	name: string;
	connectionId: string;
	model: string;
	defaultMaxTokens: number | null;
	failoverProfileId: string | null;
}

export function ModelProfileEditDialog({ profileEdit, profiles, providers, providerLabel, pending, failed, onChange, onClose, onSave }: {
	profileEdit: ModelProfileEditDraft | null;
	profiles: AIModelProfile[];
	providers: AIProviderConnection[];
	providerLabel: (kind: AIProviderKind) => string;
	pending: boolean;
	failed: boolean;
	onChange: (draft: ModelProfileEditDraft) => void;
	onClose: () => void;
	onSave: (draft: ModelProfileEditDraft) => void;
}) {
 const dialogFocus = useDialogReturnFocus();

 return (
			<Dialog
				open={profileEdit !== null}
				onOpenChange={(open) => { if (!open && !pending) onClose(); }}
			>
				<DialogContent {...dialogFocus}>
					<DialogHeader>
						<DialogTitle>Edit Model Profile</DialogTitle>
						<DialogDescription>
							Changes apply everywhere this reusable profile is
							assigned.
						</DialogDescription>
					</DialogHeader>
					{profileEdit && (
						<fieldset disabled={pending} className="grid min-w-0 gap-4 py-2">
							<div className="space-y-2">
								<Label htmlFor="edit-profile-name">Name</Label>
								<Input
									id="edit-profile-name"
									value={profileEdit.name}
									onChange={(event) =>
										onChange({
											...profileEdit,
											name: event.target.value,
										})
									}
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-profile-provider">
									Provider Connection
								</Label>
								<Select
									disabled={pending}
									value={profileEdit.connectionId}
									onValueChange={(connectionId) =>
										onChange({
											...profileEdit,
											connectionId,
											model: "",
										})
									}
								>
									<SelectTrigger className="h-auto min-h-11 w-full data-[size=default]:h-auto [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]" id="edit-profile-provider">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{providers.map((provider) => (
											<SelectItem
												key={provider.id}
												value={provider.id}
											>
												{provider.name} ·{" "}
												{providerLabel(
													provider.provider,
												)}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</div>
						<ProviderModelField
							disabled={pending}
							id="edit-profile-model"
							connectionId={profileEdit.connectionId}
							value={profileEdit.model}
							onValueChange={(model) =>
								onChange({
									...profileEdit,
									model,
								})
							}
						/>
						<div className="space-y-2">
							<Label htmlFor="edit-profile-max-tokens">Default max output tokens</Label>
							<Input
								id="edit-profile-max-tokens"
								type="number"
								min={1}
								max={200000}
								placeholder="Provider default"
								value={profileEdit.defaultMaxTokens ?? ""}
								onChange={(event) => {
									const raw = event.target.value;
									if (raw === "") {
										onChange({ ...profileEdit, defaultMaxTokens: null });
										return;
									}
									const parsed = Number.parseInt(raw, 10);
									onChange({
										...profileEdit,
										defaultMaxTokens: Number.isNaN(parsed) ? null : parsed,
									});
								}}
							/>
							<p className="text-xs text-muted-foreground">Blank = provider default. Agents with an explicit value win over this.</p>
						</div>
						<FailoverProfileField
							id="edit-profile-failover"
							profiles={profiles}
							currentProfileId={profileEdit.id}
							currentConnectionId={profileEdit.connectionId}
							value={profileEdit.failoverProfileId}
							disabled={pending}
							onChange={(failoverProfileId) =>
								onChange({
									...profileEdit,
									failoverProfileId,
								})
							}
						/>
						</fieldset>
					)}
					{failed && <p role="alert" className="text-sm text-destructive">Could not update this model profile. Your entries are preserved. Check the fields and try again.</p>}
					<DialogFooter>
						<Button className="min-h-11"
							variant="outline"
							disabled={pending}
							onClick={() => onClose()}
						>
							Cancel
						</Button>
						<Button className="min-h-11"
							disabled={
								!profileEdit?.name.trim() ||
								!profileEdit?.model.trim() ||
								!profileEdit?.connectionId ||
								pending
							}
							onClick={() =>
								profileEdit && !pending &&
								onSave(profileEdit)
							}
						>
							{pending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Saving…
								</>
							) : (
								"Save Profile"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
 );
}
