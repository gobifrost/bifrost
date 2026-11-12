import { Building2, Globe, Pin } from "lucide-react";
import { ResourceCatalogCard } from "@/components/catalog/ResourceCatalogCard";
import { ResourceIcon } from "@/components/ResourceIcon";
import { Button } from "@/components/ui/button";
import type { HomeResource } from "@/services/home";

export const resourceActions = {
	app: "Open app",
	form: "Start form",
	agent: "Chat",
};
export const resourceTypes = { app: "App", form: "Form", agent: "Agent" };

export function ResourceCard({
	resource,
	onOpen,
	onPin,
	busy,
	compact = false,
}: {
	resource: HomeResource;
	onOpen: (resource: HomeResource) => void;
	onPin: (resource: HomeResource) => void;
	busy?: boolean;
	compact?: boolean;
}) {
	return (
		<ResourceCatalogCard
			icon={
				<ResourceIcon
					kind={resource.kind}
					id={resource.id}
					icon={resource.icon}
					logo={resource.logo_url ?? null}
					cacheKey={resource.logo_version ?? undefined}
					size="card"
					className={
						resource.kind === "app"
							? "border-info/20 bg-info-soft text-info [&_svg]:text-current"
							: resource.kind === "form"
								? "border-warning/20 bg-warning-soft text-warning [&_svg]:text-current"
								: "border-power/20 bg-power-soft text-power [&_svg]:text-current"
					}
				/>
			}
			title={resource.name}
			subtitle={
				<>
					{resourceTypes[resource.kind]}
					<span className={compact ? "" : "hidden"}>
						{" "}
						· {resource.organization_name}
					</span>
				</>
			}
			description={resource.description || resourceActions[resource.kind]}
			footer={
				<p className="flex items-center gap-2">
					{resource.organization_id ? <Building2 className="size-3.5 shrink-0" /> : <Globe className="size-3.5 shrink-0" />}
					<span className="truncate">
						{resource.organization_name}
					</span>
				</p>
			}
			action={
				<Button
					variant="ghost"
					size="icon"
					className="size-11"
					aria-label={`${resource.pinned ? "Unpin" : "Pin"} ${resource.name}`}
					aria-pressed={resource.pinned}
					disabled={busy}
					onClick={() => onPin(resource)}
				>
					<Pin
						className={
							resource.pinned
								? "size-4 fill-primary text-primary"
								: "size-4"
						}
					/>
				</Button>
			}
			onOpen={() => onOpen(resource)}
			disabled={busy}
			compact={compact}
		/>
	);
}
