import { useCallback, useEffect, useRef, useState } from "react";

import { useBifrostContext } from "./provider";

interface QueryState<T> {
	data: T | null;
	loading: boolean;
	error: Error | null;
}

export interface UseApiQueryResult<T> extends QueryState<T> {
	refetch: () => Promise<void>;
}

export interface UseApiQueryOptions {
	enabled?: boolean;
}

// Keep only requests that are in flight. This lets sibling consumers and
// StrictMode's mount replay share one request without turning this tiny SDK
// helper into a cache with its own invalidation policy.
const inFlightRequests = new WeakMap<
	typeof fetch,
	Map<string, Promise<unknown>>
>();

async function httpError(response: Response): Promise<Error> {
	const body = await response.text();
	let detail = body;
	try {
		const parsed = JSON.parse(body) as {
			detail?: unknown;
			message?: unknown;
		};
		detail =
			typeof parsed.detail === "string"
				? parsed.detail
				: typeof parsed.message === "string"
					? parsed.message
					: body;
	} catch {
		// Plain-text HTTP error responses are already useful as-is.
	}
	return new Error(
		`${response.status} ${response.statusText || "Request failed"}${detail ? `: ${detail}` : ""}`,
	);
}

function readJson<T>(
	authedFetch: typeof fetch,
	path: string,
	force: boolean,
): Promise<T> {
	let requests = inFlightRequests.get(authedFetch);
	if (!requests) {
		requests = new Map();
		inFlightRequests.set(authedFetch, requests);
	}
	if (force) requests.delete(path);

	let request = requests.get(path) as Promise<T> | undefined;
	if (!request) {
		request = Promise.resolve(authedFetch(path)).then(async (response) => {
			if (!response.ok) throw await httpError(response);
			return (await response.json()) as T;
		});
		requests.set(path, request);
		void request.then(
			() => {
				if (requests?.get(path) === request) requests.delete(path);
			},
			() => {
				if (requests?.get(path) === request) requests.delete(path);
			},
		);
	}
	return request;
}

/**
 * Minimal, provider-aware GET query hook for SDK resources.
 *
 * Results are discarded when its endpoint, provider credentials/scope, or
 * component lifetime changes. This matters when a platform host switches an
 * app between organization scopes while a previous request is still pending.
 */
export function useApiQuery<T>(
	path: string,
	{ enabled = true }: UseApiQueryOptions = {},
): UseApiQueryResult<T> {
	const { authedFetch } = useBifrostContext();
	const [state, setState] = useState<QueryState<T>>({
		data: null,
		loading: enabled,
		error: null,
	});
	const [previousQuery, setPreviousQuery] = useState({
		authedFetch,
		path,
		enabled,
	});
	const requestId = useRef(0);

	// Reset during render rather than waiting for an effect. A provider scope or
	// token switch is an authorization boundary, so rendering a prior scope's
	// data for one frame is both misleading and unsafe.
	if (
		previousQuery.authedFetch !== authedFetch ||
		previousQuery.path !== path ||
		previousQuery.enabled !== enabled
	) {
		setPreviousQuery({ authedFetch, path, enabled });
		setState({ data: null, loading: enabled, error: null });
	}

	useEffect(() => {
		if (!enabled) return;
		const id = ++requestId.current;
		void readJson<T>(authedFetch, path, false).then(
			(data) => {
				if (requestId.current === id) {
					setState({ data, loading: false, error: null });
				}
			},
			(error: unknown) => {
				if (requestId.current === id) {
					setState({
						data: null,
						loading: false,
						error:
							error instanceof Error
								? error
								: new Error(String(error)),
					});
				}
			},
		);
		return () => {
			requestId.current += 1;
		};
	}, [authedFetch, enabled, path]);

	const refetch = useCallback(async () => {
		if (!enabled) return;
		// The effect-driven initial request is already loading because the
		// render-time boundary reset set it. Imperative refetches start from an
		// event handler, so they explicitly expose their loading transition.
		const id = ++requestId.current;
		setState((previous) => ({ ...previous, loading: true, error: null }));
		try {
			const data = await readJson<T>(authedFetch, path, true);
			if (requestId.current === id)
				setState({ data, loading: false, error: null });
		} catch (error: unknown) {
			if (requestId.current === id) {
				setState({
					data: null,
					loading: false,
					error:
						error instanceof Error
							? error
							: new Error(String(error)),
				});
			}
		}
	}, [authedFetch, enabled, path]);

	return { ...state, refetch };
}
