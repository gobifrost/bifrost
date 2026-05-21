import { useState, type ReactNode } from "react";
import { useEntityLogoVersion } from "./entityLogoVersions";

export type EntityLogoProps = {
	entityType: "app" | "agent";
	entityId: string;
	fallback: ReactNode;
	size: number;
	cacheKey?: string;
	className?: string;
	/**
	 * Inline logo (data URL) from the list/detail response. When provided as a
	 * string, renders directly — no extra GET. When explicitly `null`, renders
	 * the fallback without hitting the per-entity endpoint. When `undefined`,
	 * falls back to fetching /api/{type}/{id}/logo (preserves the upload
	 * dialog's live-preview behavior).
	 */
	logo?: string | null;
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
	logo,
}: EntityLogoProps) {
	const [erroredVersion, setErroredVersion] = useState<string | null>(null);
	const globalVersion = useEntityLogoVersion(entityType, entityId);

	if (logo === null) {
		return <>{fallback}</>;
	}

	if (typeof logo === "string") {
		return (
			<img
				data-testid="entity-logo"
				src={logo}
				alt=""
				width={size}
				height={size}
				className={className}
			/>
		);
	}

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
