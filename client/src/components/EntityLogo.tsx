import { useState, type ReactNode } from "react";

import { useEntityLogoVersion } from "./entityLogoVersions";

export type EntityLogoProps = {
	entityType: "app" | "agent" | "solution";
	entityId: string;
	fallback: ReactNode;
	size: number;
	cacheKey?: string;
	className?: string;
	/**
	 * Logo source from the list/detail response. A string is rendered directly,
	 * null means there is no logo, and undefined retains the endpoint behavior
	 * used by upload surfaces.
	 */
	logo?: string | null;
};

const PATHS: Record<EntityLogoProps["entityType"], string> = {
	app: "/api/applications",
	agent: "/api/agents",
	solution: "/api/solutions",
};

export function EntityLogo({
	entityType,
	entityId,
	fallback,
	size,
	cacheKey,
	className,
	logo,
}: EntityLogoProps) {
	const [erroredSource, setErroredSource] = useState<string | null>(null);
	const [loadedSource, setLoadedSource] = useState<string | null>(null);
	const globalVersion = useEntityLogoVersion(entityType, entityId);
	const base = `${PATHS[entityType]}/${entityId}/logo`;
	const effectiveKey = cacheKey ?? globalVersion?.toString() ?? null;
	const endpointSource = effectiveKey
		? `${base}?v=${encodeURIComponent(effectiveKey)}`
		: base;
	const src = logo === null ? null : (logo ?? endpointSource);
	const hasUsableSource = src !== null && erroredSource !== src;
	const imageLoaded = src !== null && loadedSource === src;

	return (
		<span
			className={`relative inline-grid place-items-center overflow-hidden ${className ?? ""}`}
			style={{ width: size, height: size }}
		>
			<span className="absolute inset-0 grid place-items-center">
				{fallback}
			</span>
			{hasUsableSource ? (
				<img
					data-testid="entity-logo"
					src={src}
					alt=""
					width={size}
					height={size}
					className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-150 motion-reduce:transition-none ${imageLoaded ? "opacity-100" : "opacity-0"}`}
					onLoad={() => setLoadedSource(src)}
					onError={() => setErroredSource(src)}
				/>
			) : null}
		</span>
	);
}
