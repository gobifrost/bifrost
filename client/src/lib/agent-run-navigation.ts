export interface AgentRunNavigationOrigin {
	href: string;
	label: string;
}

export interface AgentRunNavigationState {
	agentRunOrigin: AgentRunNavigationOrigin;
}

interface LocationHref {
	pathname: string;
	search?: string;
	hash?: string;
}

export function getLocationHref(location: LocationHref): string {
	return `${location.pathname}${location.search ?? ""}${location.hash ?? ""}`;
}

export function createAgentRunNavigationState(
	origin: AgentRunNavigationOrigin,
): AgentRunNavigationState {
	return { agentRunOrigin: origin };
}

export function readAgentRunNavigationOrigin(
	state: unknown,
): AgentRunNavigationOrigin | null {
	if (!state || typeof state !== "object") return null;

	const origin = (state as { agentRunOrigin?: unknown }).agentRunOrigin;
	if (!origin || typeof origin !== "object") return null;

	const { href, label } = origin as {
		href?: unknown;
		label?: unknown;
	};
	if (
		typeof href !== "string" ||
		typeof label !== "string" ||
		label.trim().length === 0 ||
		!isSafeInternalHref(href)
	) {
		return null;
	}

	return { href, label: label.trim() };
}

function isSafeInternalHref(href: string): boolean {
	if (
		!href.startsWith("/") ||
		href.startsWith("//") ||
		href.includes("\\")
	) {
		return false;
	}

	try {
		const base = "https://bifrost.local";
		return new URL(href, base).origin === base;
	} catch {
		return false;
	}
}
