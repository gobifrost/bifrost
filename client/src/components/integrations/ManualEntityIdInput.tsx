import { useState } from "react";
import { Input } from "@/components/ui/input";

export function ManualEntityIdInput({
	orgId,
	value,
	onCommit,
	disabled = false,
}: {
	orgId: string;
	value: string;
	onCommit: (orgId: string, entityId: string, entityName: string) => void;
	disabled?: boolean;
}) {
	// Pristine fields follow refreshed data. Keep an unsaved draft across refetches.
	const [draft, setDraft] = useState<string | null>(null);
	if (draft !== null && draft === value) setDraft(null);
	const local = draft ?? value;

	return (
		<Input
			aria-label="External entity ID"
			className="min-h-11"
			disabled={disabled}
			value={local}
			onChange={(e) =>
				setDraft(e.target.value === value ? null : e.target.value)
			}
			onBlur={() => {
				if (local !== value) {
					onCommit(orgId, local, local);
				}
			}}
			placeholder="Entity ID"
		/>
	);
}
