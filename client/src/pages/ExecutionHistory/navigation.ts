export interface ExecutionHistoryOrigin {
	href: string;
	scrollTop: number;
	currentToken?: string;
	pageStack?: (string | null)[];
}

interface ExecutionHistoryOriginState {
	executionHistoryOrigin: ExecutionHistoryOrigin;
}

interface ExecutionHistoryRestoreState {
	executionHistoryRestore: ExecutionHistoryOrigin;
}

export function createExecutionHistoryOriginState(
	origin: ExecutionHistoryOrigin,
): ExecutionHistoryOriginState {
	return { executionHistoryOrigin: origin };
}

export function createExecutionHistoryRestoreState(
	origin: ExecutionHistoryOrigin,
): ExecutionHistoryRestoreState {
	return { executionHistoryRestore: origin };
}

export function readExecutionHistoryOrigin(
	state: unknown,
): ExecutionHistoryOrigin | null {
	return readOrigin(state, "executionHistoryOrigin");
}

export function readExecutionHistoryRestore(
	state: unknown,
): ExecutionHistoryOrigin | null {
	return readOrigin(state, "executionHistoryRestore");
}

function readOrigin(
	state: unknown,
	key: string,
): ExecutionHistoryOrigin | null {
	if (!state || typeof state !== "object") return null;
	const origin = (state as Record<string, unknown>)[key];
	if (!origin || typeof origin !== "object") return null;

	const { href, scrollTop, currentToken, pageStack } = origin as {
		href?: unknown;
		scrollTop?: unknown;
		currentToken?: unknown;
		pageStack?: unknown;
	};
	if (
		typeof href !== "string" ||
		!isHistoryHref(href) ||
		typeof scrollTop !== "number" ||
		!Number.isFinite(scrollTop) ||
		scrollTop < 0 ||
		(currentToken !== undefined && typeof currentToken !== "string") ||
		(pageStack !== undefined &&
			(!Array.isArray(pageStack) ||
				pageStack.some(
					(token) => token !== null && typeof token !== "string",
				)))
	) {
		return null;
	}

	return {
		href,
		scrollTop,
		...(currentToken === undefined ? {} : { currentToken }),
		...(pageStack === undefined ? {} : { pageStack }),
	};
}

function isHistoryHref(href: string): boolean {
	if (!href.startsWith("/history") || href.startsWith("//")) return false;
	try {
		const base = "https://bifrost.local";
		const url = new URL(href, base);
		return url.origin === base && url.pathname === "/history";
	} catch {
		return false;
	}
}
