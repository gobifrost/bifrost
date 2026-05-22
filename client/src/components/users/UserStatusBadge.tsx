import { Badge } from "@/components/ui/badge";

interface Props {
	status: string;
}

const LABELS: Record<
	string,
	{ text: string; variant: "default" | "secondary" | "outline" | "destructive" }
> = {
	active: { text: "Active", variant: "default" },
	pending: { text: "Pending invite", variant: "secondary" },
	expired: { text: "Invite expired", variant: "destructive" },
	never_invited: { text: "Not invited", variant: "outline" },
};

export function UserStatusBadge({ status }: Props) {
	const cfg = LABELS[status] ?? LABELS.active;
	return (
		<Badge variant={cfg.variant} className="text-xs">
			{cfg.text}
		</Badge>
	);
}
