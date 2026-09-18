import { CheckCircle2, Loader2, MessageSquareText, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import type { AIModelProfile } from "@/services/aiModels";

export function ModelProfileCard({ profile, description, assignments, selectionMode, selected, isDefault, chatPending, defaultPending, defaultDisabled, onSelect, onEdit, onDelete, onChatChange, onSetDefault }: {
	profile: AIModelProfile;
	description: string;
	assignments: string[];
	selectionMode: boolean;
	selected: boolean;
	isDefault: boolean;
	chatPending: boolean;
	defaultPending: boolean;
	defaultDisabled: boolean;
	onSelect: (selected: boolean) => void;
	onEdit: () => void;
	onDelete: () => void;
	onChatChange: (enabled: boolean) => void;
	onSetDefault: () => void;
}) {
	return <Card size="sm" className={`min-w-0 transition-colors motion-reduce:transition-none ${selected ? "bg-primary/[0.03] ring-primary/25" : ""}`}>
		<CardHeader>
			{selectionMode && <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm"><Checkbox checked={selected} onCheckedChange={(checked) => onSelect(checked === true)} aria-label={`Select ${profile.name}`} />Select for merge</label>}
			<CardTitle className="min-w-0 text-base [overflow-wrap:anywhere]">{profile.name}</CardTitle>
			<CardDescription className="min-w-0 [overflow-wrap:anywhere]">{description}</CardDescription>
		</CardHeader>
		<CardContent className="space-y-4">
		{(profile.enabled_for_chat || isDefault || assignments.length > 0 || profile.default_max_tokens != null || profile.failover_profile_name != null) && <div className="flex flex-wrap gap-2">
			{profile.enabled_for_chat && <Badge variant="secondary"><MessageSquareText className="size-3" />Chat</Badge>}
			{isDefault && <Badge><CheckCircle2 className="size-3" />Default</Badge>}
			{profile.default_max_tokens != null && <Badge variant="outline" className="h-auto whitespace-normal">Default cap {profile.default_max_tokens?.toLocaleString()}</Badge>}
			{profile.failover_profile_name != null && <Badge variant="outline" className="h-auto whitespace-normal [overflow-wrap:anywhere]">Fallback → {profile.failover_profile_name}</Badge>}
			{assignments.map((name) => <Badge key={name} variant="outline" className="h-auto whitespace-normal [overflow-wrap:anywhere]">{name}</Badge>)}
		</div>}
			<div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t pt-3">
				<label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm"><Switch checked={profile.enabled_for_chat} disabled={selectionMode || chatPending} onCheckedChange={onChatChange} aria-label={`Enable ${profile.name} for Chat`} />Enabled for Chat</label>
				<Button type="button" variant="outline" className="min-h-11" disabled={selectionMode || isDefault || defaultDisabled} onClick={onSetDefault}>{defaultPending ? <><Loader2 className="size-4 animate-spin motion-reduce:animate-none" />Saving…</> : isDefault ? <><CheckCircle2 className="size-4" />Default</> : "Set Default"}</Button>
			</div>
			{!selectionMode && <div role="group" aria-label={`Actions for ${profile.name}`} className="flex flex-wrap gap-2">
				<Button type="button" variant="outline" className="min-h-11" aria-label={`Edit ${profile.name}`} onClick={onEdit}><Pencil className="size-4" />Edit</Button>
				<Button type="button" variant="ghost" className="min-h-11" aria-label={`Delete ${profile.name}`} onClick={onDelete}><Trash2 className="size-4" />Delete</Button>
			</div>}
		</CardContent>
	</Card>;
}
