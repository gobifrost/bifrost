import { useState, type ReactNode } from "react";
import { useEntityLogoVersion } from "./entityLogoVersions";

export type EntityLogoProps = {
	entityType: "app" | "agent";
	entityId: string;
	fallback: ReactNode;
	size: number;
	cacheKey?: string;
	className?: string;
};

const PATHS: Record<EntityLogoProps["entityType"], string> = {
	app: "/api/applications",
	agent: "/api/agents",
};

export function EntityLogo({
	entityType,
	entityId,
	fallback,
	size,
	cacheKey,
	className,
}: EntityLogoProps) {
	// Track which "version" of the URL we last failed to load. When a new
	// version is bumped (after upload/delete), this stale marker no longer
	// matches and we retry — no setState-in-useEffect dance needed.
	const [erroredVersion, setErroredVersion] = useState<string | null>(null);

	// Global per-entity version bumped by LogoDropZone after upload/delete.
	const globalVersion = useEntityLogoVersion(entityType, entityId);

	const base = `${PATHS[entityType]}/${entityId}/logo`;
	const effectiveKey = cacheKey ?? globalVersion?.toString() ?? null;
	const src = effectiveKey
		? `${base}?v=${encodeURIComponent(effectiveKey)}`
		: base;

	if (erroredVersion !== null && erroredVersion === (effectiveKey ?? "")) {
		return <>{fallback}</>;
	}

	return (
		<img
			data-testid="entity-logo"
			src={src}
			alt=""
			width={size}
			height={size}
			className={className}
			onError={() => setErroredVersion(effectiveKey ?? "")}
		/>
	);
}
