import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { AIModelProfile } from "@/services/aiModels";

export function FailoverProfileField({ id, profiles, currentProfileId, currentConnectionId, value, disabled, onChange }: {
	id: string;
	profiles: AIModelProfile[];
	currentProfileId: string | null;
	currentConnectionId: string;
	value: string | null;
	disabled?: boolean;
	onChange: (profileId: string | null) => void;
}) {
	const options = profiles.filter((profile) => profile.id !== currentProfileId);
	const selected = options.find((profile) => profile.id === value) ?? null;
	const sameConnection = selected !== null && selected.connection_id === currentConnectionId;

	return (
		<div className="space-y-2">
			<Label htmlFor={id}>Failover profile</Label>
			<Select
				disabled={disabled}
				value={value ?? "none"}
				onValueChange={(next) => onChange(next === "none" ? null : next)}
			>
				<SelectTrigger className="h-auto min-h-11 w-full data-[size=default]:h-auto [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]" id={id}>
					<SelectValue placeholder="None — fail the run instead" />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="none">None — fail the run instead</SelectItem>
					{options.map((profile) => (
						<SelectItem key={profile.id} value={profile.id}>
							{profile.name} · {profile.model}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<p className="text-xs text-muted-foreground">
				Tried once when this profile&apos;s provider fails with a server
				error after retries are exhausted. Other errors still fail the run.
			</p>
			{sameConnection && (
				<p role="note" className="text-xs text-muted-foreground">
					Same connection as this profile — covers single-model incidents
					only. A different connection isolates quota outages too.
				</p>
			)}
		</div>
	);
}
