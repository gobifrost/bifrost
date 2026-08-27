import type { EventSource } from "@/services/events";

export type GraphSubscriptionHealth = "connected" | "expired" | "attention";

export type GraphSourceSummary = {
	userLabel: string;
	userSecondary: string | null;
	resourceLabel: string;
	resourcePath: string | null;
	changeLabel: string;
	health: GraphSubscriptionHealth;
};

const RESOURCE_LABELS: Record<string, string> = {
	messages: "Mail messages",
	events: "Calendar events",
	mailFolders: "Mail folders",
	contacts: "Contacts",
	callRecords: "Call records",
};

function stringValue(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value : null;
}

function compactId(value: string): string {
	return value.length > 16
		? `${value.slice(0, 8)}…${value.slice(-4)}`
		: value;
}

export function isMicrosoftGraphSource(source: EventSource): boolean {
	return source.webhook?.adapter_name === "microsoft_graph";
}

export function getGraphSourceSummary(
	source: EventSource,
	now = new Date(),
): GraphSourceSummary | null {
	if (!isMicrosoftGraphSource(source) || !source.webhook) return null;

	const metadata = source.webhook.provider_metadata ?? {};
	const config = source.webhook.config ?? {};
	const userId =
		stringValue(metadata.user_id) ?? stringValue(config.user_id);
	const displayName = stringValue(metadata.user_display_name);
	const principalName =
		stringValue(metadata.user_principal_name) ??
		stringValue(metadata.user_mail);
	const resourcePath =
		stringValue(metadata.resource) ?? stringValue(config.resource);
	const resourceKey = resourcePath?.replace(/\/$/, "").split("/").at(-1);
	const rawChanges = metadata.change_types ?? config.change_types;
	const changes = Array.isArray(rawChanges)
		? rawChanges.filter((value): value is string => typeof value === "string")
		: [];

	let health: GraphSubscriptionHealth = "connected";
	if (source.error_message || !source.webhook.external_id) {
		health = "attention";
	} else if (
		source.webhook.expires_at &&
		new Date(source.webhook.expires_at).getTime() <= now.getTime()
	) {
		health = "expired";
	}

	return {
		userLabel:
			displayName ?? principalName ?? (userId ? compactId(userId) : "Graph user"),
		userSecondary:
			displayName && principalName && displayName !== principalName
				? principalName
				: null,
		resourceLabel:
			(resourceKey && RESOURCE_LABELS[resourceKey]) ??
			resourceKey ??
			"Graph resource",
		resourcePath,
		changeLabel: changes.length ? changes.join(", ") : "All changes",
		health,
	};
}
