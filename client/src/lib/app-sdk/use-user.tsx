/**
 * The signed-in user, for display and for showing or hiding controls.
 *
 * `useUser()` reads `GET /api/auth/me` once per provider and shares the
 * result with every consumer. Same shape as the v1 app hook (`id`, `email`,
 * `name`, `roles`, `hasRole()`, `organizationId`), plus `isLoading`, `error`
 * and `isPlatformAdmin`.
 *
 * Hiding a control is a convenience, not a security boundary: the workflow
 * (its access level and roles, its own checks on `context`) and table
 * policies decide what actually happens. Never send these values to a
 * workflow as input; the workflow already knows who is calling.
 */
import { type ReactElement, type ReactNode, useEffect, useState } from "react";

import { useBifrostContext } from "./provider";

interface MeResponse {
	id: string;
	email: string;
	name: string;
	is_superuser: boolean;
	organization_id: string | null;
	roles: string[];
}

export interface BifrostUser {
	id: string;
	email: string;
	name: string;
	/** Role names the user holds in this session. */
	roles: string[];
	/** Whether the user holds the role with this name. */
	hasRole: (role: string) => boolean;
	/** The user's organization id, or "" for a user with no organization. */
	organizationId: string;
	isPlatformAdmin: boolean;
	isLoading: boolean;
	error: Error | null;
}

type MeState = { me: MeResponse | null; error: Error | null };

// One request per provider: the provider's authedFetch identity changes only
// when the provider's own inputs do, so it keys the shared request.
const requests = new WeakMap<typeof fetch, Promise<MeState>>();

function requestMe(authedFetch: typeof fetch): Promise<MeState> {
	let request = requests.get(authedFetch);
	if (!request) {
		request = authedFetch("/api/auth/me").then(
			async (response) =>
				response.ok
					? { me: (await response.json()) as MeResponse, error: null }
					: { me: null, error: new Error(`Could not read the signed-in user (${response.status})`) },
			(error: unknown) => ({
				me: null,
				error: error instanceof Error ? error : new Error(String(error)),
			}),
		);
		// A failed read isn't kept: the next consumer to mount asks again.
		request.then((state) => {
			if (state.error && requests.get(authedFetch) === request) requests.delete(authedFetch);
		});
		requests.set(authedFetch, request);
	}
	return request;
}

export function useUser(): BifrostUser {
	const { authedFetch } = useBifrostContext();
	const [state, setState] = useState<MeState | null>(null);

	useEffect(() => {
		let active = true;
		requestMe(authedFetch).then((next) => {
			if (active) setState(next);
		});
		return () => {
			active = false;
		};
	}, [authedFetch]);

	const me = state?.me ?? null;
	const roles = me?.roles ?? [];
	return {
		id: me?.id ?? "",
		email: me?.email ?? "",
		name: me?.name ?? "",
		roles,
		hasRole: (role: string) => roles.includes(role),
		organizationId: me?.organization_id ?? "",
		isPlatformAdmin: me?.is_superuser ?? false,
		isLoading: state === null,
		error: state?.error ?? null,
	};
}

export interface RequireRoleProps {
	role: string;
	children: ReactNode;
	/** Rendered when the user doesn't hold the role. Nothing renders while loading. */
	fallback?: ReactNode;
}

/** Renders `children` only for a user holding `role`. Display only; the server enforces. */
export function RequireRole({ role, children, fallback = null }: RequireRoleProps): ReactElement | null {
	const user = useUser();
	if (user.isLoading) return null;
	return <>{user.hasRole(role) ? children : fallback}</>;
}
