import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { Button } from "@/components/ui/button";
import { useBaseRoles } from "@/hooks/useRoles";
import { USER_ROLE_ID } from "@/lib/builtin-roles";

const DEFAULT_VALUE = "__default__";

/**
 * A base-role picker. `null` means use the caller-supplied default behaviour,
 * which keeps creation and policy screens explicit without inventing another
 * role representation in the client.
 */
export function BaseRoleSelect({
	id,
	value,
	onChange,
	disabled = false,
	defaultLabel = "Use the default base role",
	defaultDescription,
	ariaLabel = "Base role",
}: {
	id?: string;
	value: string | null;
	onChange: (value: string | null) => void;
	disabled?: boolean;
	defaultLabel?: string;
	defaultDescription?: string;
	ariaLabel?: string;
}) {
	const roles = useBaseRoles();
	const options: ComboboxOption[] = [
		{
			value: DEFAULT_VALUE,
			label: defaultLabel,
			description: defaultDescription,
		},
		...(roles.data ?? [])
			// Custom roles may become base roles. Of the built-ins, only User
			// can be a base role; Platform Admin and operator roles are always
			// additional roles.
			.filter((role) => !role.is_builtin || role.id === USER_ROLE_ID)
			.map((role) => ({
				value: role.id,
				label: role.name,
				description: role.description ?? undefined,
			})),
	];

	return (
		<>
			<Combobox
				id={id}
				aria-label={ariaLabel}
				value={value ?? DEFAULT_VALUE}
				onValueChange={(next) =>
					onChange(next === DEFAULT_VALUE ? null : next)
				}
				options={options}
				disabled={disabled || roles.isError}
				isLoading={roles.isLoading}
				placeholder="Select a base role..."
				searchPlaceholder="Search base roles..."
				emptyText="No base roles found."
				showSelectedDescription
			/>
			{roles.isError && (
				<div
					role="alert"
					className="flex flex-wrap items-center gap-2 text-sm text-destructive"
				>
					<span>Base roles could not be loaded.</span>
					<Button
						type="button"
						variant="outline"
						size="sm"
						disabled={roles.isFetching}
						onClick={() => void roles.refetch()}
					>
						Retry Roles
					</Button>
				</div>
			)}
		</>
	);
}
