import { useState, type ReactNode } from "react";

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
	const [errored, setErrored] = useState(false);

	if (errored) {
		return <>{fallback}</>;
	}

	const base = `${PATHS[entityType]}/${entityId}/logo`;
	const src = cacheKey ? `${base}?v=${encodeURIComponent(cacheKey)}` : base;

	return (
		<img
			data-testid="entity-logo"
			src={src}
			alt=""
			width={size}
			height={size}
			className={className}
			onError={() => setErrored(true)}
		/>
	);
}
