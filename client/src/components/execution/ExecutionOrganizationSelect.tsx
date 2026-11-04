import { useId } from "react";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";

/** Select execution scope without changing the signed-in user or resource ownership. */
export function ExecutionOrganizationSelect({
	value,
	onChange,
	organizationId,
	disabled,
	resetInputs = false,
	resourceType = "workflow",
	contentClassName,
}: {
	value: string | undefined;
	onChange: (organizationId: string | undefined) => void;
	organizationId?: string | null;
	disabled?: boolean;
	resetInputs?: boolean;
	resourceType?: "workflow" | "form";
	contentClassName?: string;
}) {
	const id = useId();
	const authorization = useAuthorization();
	const homeId = authorization.authorization?.home_organization_id;
	const homeName = authorization.authorization?.home_organization_name;
	const defaultId = organizationId ?? homeId ?? null;
	const canDiscover = authorization.canAnywhere("organizations.read");
	return (
		<div className="min-w-0 space-y-2">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<Label htmlFor={id}>Run in Organization</Label>
				{value && !organizationId && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						disabled={disabled}
						onClick={() => onChange(undefined)}
					>
						Use Default
					</Button>
				)}
			</div>
			<OrganizationSelect
				id={id}
				aria-label="Run in Organization"
				contentClassName={contentClassName}
				aria-describedby={`${id}-help`}
				value={value ?? defaultId}
				onChange={(next) => {
					if (next) onChange(next === defaultId ? undefined : next);
				}}
				showGlobal={false}
				disabled={disabled || !!organizationId || !canDiscover}
				loadOrganizations={canDiscover}
				knownOrganizations={
					homeId && homeName ? [{ id: homeId, name: homeName }] : []
				}
				filterOrganizations={(org) =>
					authorization.canAt(
						"workflows.execute",
						orgTarget(org.id),
					) &&
					(resourceType !== "form" ||
						authorization.canAt("forms.read", orgTarget(org.id)) ||
						authorization.canAt(
							"forms.readbasic",
							orgTarget(org.id),
						))
				}
			/>
			<p id={`${id}-help`} className="text-sm text-muted-foreground">
				{organizationId
					? `This ${resourceType} runs in its own organization.`
					: resetInputs
						? "Changing organization reloads form data and clears your entries."
						: "Uses this organization’s data and integrations. You stay signed in as yourself."}
			</p>
		</div>
	);
}
