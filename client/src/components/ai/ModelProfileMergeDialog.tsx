import type { RefObject } from "react";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { GitMerge, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { AIModelProfile } from "@/services/aiModels";
import { useModelDisplayName } from "@/services/modelNames";

export function ModelProfileMergeDialog({ profileMergeOpen, selectedProfiles, mergeTargetProfileId, setMergeTargetProfileId, pending, failed, completed = false, returnFocusRef, onClose, onConfirm }: {
 profileMergeOpen: boolean;
 selectedProfiles: AIModelProfile[];
 mergeTargetProfileId: string;
 setMergeTargetProfileId: (id: string) => void;
 returnFocusRef?: RefObject<HTMLElement | null>;
 completed?: boolean;
 pending: boolean;
 failed: boolean;
 onClose: () => void;
 onConfirm: () => void;
}) {
	const modelName = useModelDisplayName();
	const mergeTargetProfile = selectedProfiles.find(
		(profile) => profile.id === mergeTargetProfileId,
	);
	const mergeSourceProfiles = selectedProfiles.filter(
		(profile) => profile.id !== mergeTargetProfileId,
	);
	const mergeAgentCount = mergeSourceProfiles.reduce(
		(total, profile) => total + profile.referenced_agent_count,
		0,
	);
	const mergeAssignmentCount = new Set(
		mergeSourceProfiles.flatMap((profile) => profile.assignment_keys ?? []),
	).size;
	const mergeEnablesChat =
		mergeTargetProfile !== undefined &&
		!mergeTargetProfile.enabled_for_chat &&
		selectedProfiles.some((profile) => profile.enabled_for_chat);

 const dialogFocus = useDialogReturnFocus(returnFocusRef, completed);

	return (
			<Dialog
				open={profileMergeOpen}
				onOpenChange={(open) => {
					if (pending) return;
					if (!open) onClose();
				}}
			>
				<DialogContent {...dialogFocus}>
					<DialogHeader>
						<DialogTitle>Merge Model Profiles</DialogTitle>
						<DialogDescription>
							Choose the profile to keep. Every assignment and
							agent using the others will switch to it.
						</DialogDescription>
					</DialogHeader>
					<div className="grid gap-4 py-2">
						<RadioGroup
							disabled={pending}
							value={mergeTargetProfileId}
							onValueChange={setMergeTargetProfileId}
							aria-label="Profile to keep"
							className="gap-2"
						>
							{selectedProfiles.map((profile) => {
								const selected =
									profile.id === mergeTargetProfileId;
								return (
									<Label
										key={profile.id}
										htmlFor={`merge-target-${profile.id}`}
										className={`flex min-h-11 min-w-0 cursor-pointer items-start gap-3 rounded-[var(--bf-radius-surface)] border p-4 transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none ${
											selected
												? "border-primary bg-primary/5"
												: "hover:bg-muted/50"
										}`}
									>
										<RadioGroupItem
											id={`merge-target-${profile.id}`}
											value={profile.id}
										/>
										<span className="min-w-0 flex-1">
											<span className="block font-medium [overflow-wrap:anywhere]">
												{profile.name}
											</span>
											<span className="mt-1 block text-sm font-normal text-muted-foreground [overflow-wrap:anywhere]">
												{`${profile.connection.name} · ${modelName(profile.model)}`}
											</span>
											{selected && <Badge variant="secondary" className="mt-2">Keep</Badge>}
										</span>
									</Label>
								);
							})}
						</RadioGroup>

						{mergeTargetProfile && (
							<div
								className="rounded-[var(--bf-radius-surface)] bg-muted/50 p-4 text-sm [overflow-wrap:anywhere]"
								aria-live="polite"
							>
								<p className="font-medium">
									Keep {mergeTargetProfile.name} and remove{" "}
									{mergeSourceProfiles.length} other{" "}
									{mergeSourceProfiles.length === 1
										? "profile"
										: "profiles"}
									.
								</p>
								<p className="mt-1 text-muted-foreground">
									{mergeAgentCount}{" "}
									{mergeAgentCount === 1 ? "agent" : "agents"}{" "}
									and {mergeAssignmentCount}{" "}
									{mergeAssignmentCount === 1
										? "assignment"
										: "assignments"}{" "}
									will move to it.
								</p>
								{mergeEnablesChat && (
									<p className="mt-1 text-muted-foreground">
										{mergeTargetProfile.name} will also be
										enabled for Chat.
									</p>
								)}
								<p className="mt-3 text-xs font-medium text-destructive">
									This can’t be undone.
								</p>
							</div>
						)}
					</div>
					{failed && <p role="alert" className="text-sm text-destructive">Could not merge these profiles. Your selection is preserved. Review the profiles and try again.</p>}
					<DialogFooter>
						<Button className="min-h-11"
							variant="outline"
							disabled={pending}
							onClick={() => onClose()}
						>
							Cancel
						</Button>
						<Button className="min-h-11"
							variant="destructive"
							disabled={
								!mergeTargetProfileId ||
								selectedProfiles.length < 2 ||
								pending
							}
							onClick={onConfirm}
						>
							{pending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Merging…
								</>
							) : (
								<>
									<GitMerge className="h-4 w-4" />
									Merge Profiles
								</>
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
	);
}
