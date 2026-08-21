import {
	createContext,
	type ReactNode,
	useCallback,
	useContext,
	useMemo,
	useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/contexts/AuthContext";
import { isEmbedSession } from "@/lib/auth-token";
import {
	getSelectedAuthorizationBoundary,
	storeSelectedAuthorizationBoundary,
} from "@/lib/authorization-boundary";
import {
	getAuthorizationTargets,
	type AuthorizationTarget,
} from "@/services/authorizationTargets";

interface AuthorizationBoundaryContextValue {
	targets: AuthorizationTarget[];
	selectedTarget: AuthorizationTarget | undefined;
	selectedBoundary: string | undefined;
	setSelectedBoundary: (boundary: string) => void;
	hasSelectedCapability: (capability: string) => boolean;
	isLoading: boolean;
}

const AuthorizationBoundaryContext =
	createContext<AuthorizationBoundaryContextValue | null>(null);

function defaultTarget(
	targets: AuthorizationTarget[],
	homeOrganizationId: string | null | undefined,
): AuthorizationTarget | undefined {
	return (
		targets.find(
			(target) => target.organization_id === homeOrganizationId,
		) ??
		targets.find((target) => target.kind === "organization") ??
		targets[0]
	);
}

export function targetHasCapability(
	target: AuthorizationTarget | undefined,
	capability: string,
): boolean {
	const capabilities = target?.capabilities ?? [];
	return (
		capabilities.includes("platform.superuser") ||
		capabilities.includes(capability)
	);
}

export function AuthorizationBoundaryProvider({
	children,
}: {
	children: ReactNode;
}) {
	const { isAuthenticated, user } = useAuth();
	const [selectedBoundaryState, setSelectedBoundaryState] = useState<{
		userId: string;
		boundary: string;
	} | null>(null);
	const canDiscover = isAuthenticated && !isEmbedSession();
	const { data, isLoading } = useQuery({
		queryKey: ["authorization-targets", user?.id],
		queryFn: getAuthorizationTargets,
		enabled: canDiscover,
		staleTime: 60_000,
	});
	const targets = useMemo(() => data?.targets ?? [], [data?.targets]);
	const storedBoundary = user
		? getSelectedAuthorizationBoundary(user.id)
		: undefined;
	const selectedBoundary = useMemo(() => {
		if (!canDiscover || !user) return undefined;
		const explicitBoundary =
			selectedBoundaryState?.userId === user.id
				? selectedBoundaryState.boundary
				: undefined;
		const resolveBoundary = (boundary: string | undefined) =>
			boundary && targets.some((target) => target.boundary === boundary)
				? boundary
				: undefined;
		return (
			resolveBoundary(explicitBoundary) ??
			resolveBoundary(storedBoundary) ??
			defaultTarget(targets, user.organizationId)?.boundary
		);
	}, [canDiscover, selectedBoundaryState, storedBoundary, targets, user]);

	const setSelectedBoundary = useCallback(
		(boundary: string) => {
			if (!user || !targets.some((target) => target.boundary === boundary)) {
				return;
			}
			setSelectedBoundaryState({ userId: user.id, boundary });
			storeSelectedAuthorizationBoundary(user.id, boundary);
		},
		[targets, user],
	);

	const selectedTarget = useMemo(
		() =>
			targets.find((target) => target.boundary === selectedBoundary),
		[selectedBoundary, targets],
	);
	const hasSelectedCapability = useCallback(
		(capability: string) => targetHasCapability(selectedTarget, capability),
		[selectedTarget],
	);
	const value = useMemo(
		() => ({
			targets,
			selectedTarget,
			selectedBoundary,
			setSelectedBoundary,
			hasSelectedCapability,
			isLoading: canDiscover && isLoading,
		}),
		[
			canDiscover,
			hasSelectedCapability,
			isLoading,
			selectedBoundary,
			targets,
			selectedTarget,
			setSelectedBoundary,
		],
	);

	return (
		<AuthorizationBoundaryContext.Provider value={value}>
			{children}
		</AuthorizationBoundaryContext.Provider>
	);
}

export function useAuthorizationBoundary() {
	const context = useContext(AuthorizationBoundaryContext);
	if (!context) {
		throw new Error(
			"useAuthorizationBoundary must be used within AuthorizationBoundaryProvider",
		);
	}
	return context;
}
