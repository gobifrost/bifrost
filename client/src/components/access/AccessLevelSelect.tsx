import { Globe, Shield, Users } from "lucide-react";
import type { AriaAttributes } from "react";

import { Combobox, type ComboboxOption } from "@/components/ui/combobox";

export type AccessLevelValue = "authenticated" | "everyone" | "role_based";

export interface AccessLevelOption extends ComboboxOption {
	value: AccessLevelValue | string;
	icon: typeof Shield;
}

interface AccessLevelSelectProps {
	value: string;
	onValueChange: (value: string) => void;
	disabled?: boolean;
	includeNoChange?: boolean;
	noChangeValue?: string;
	className?: string;
	id?: string;
	"aria-label"?: string;
	"aria-describedby"?: string;
	"aria-invalid"?: AriaAttributes["aria-invalid"];
}

const BASE_ACCESS_OPTIONS: AccessLevelOption[] = [
	{
		value: "role_based",
		label: "Role-based",
		description: "Only users with assigned roles can access",
		icon: Shield,
	},
	{
		value: "authenticated",
		label: "Everyone except external users",
		description: "Any signed-in user except external users",
		icon: Users,
	},
	{
		value: "everyone",
		label: "Everyone",
		description: "Any signed-in user, including external users",
		icon: Globe,
	},
];

export function accessLevelOptions({
	includeNoChange = false,
	noChangeValue = "__no_change__",
}: {
	includeNoChange?: boolean;
	noChangeValue?: string;
} = {}): AccessLevelOption[] {
	return [
		...(includeNoChange
			? [
					{
						value: noChangeValue,
						label: "No change",
						description: "Leave access level unchanged",
						icon: Shield,
					},
				]
			: []),
		...BASE_ACCESS_OPTIONS,
	];
}

export function AccessLevelSelect({
	value,
	onValueChange,
	disabled = false,
	includeNoChange = false,
	noChangeValue = "__no_change__",
	className,
	id,
	"aria-label": ariaLabel,
	"aria-describedby": describedBy,
	"aria-invalid": invalid,
}: AccessLevelSelectProps) {
	return (
		<Combobox
			id={id}
			value={value}
			onValueChange={onValueChange}
			options={accessLevelOptions({
				includeNoChange,
				noChangeValue,
			})}
			placeholder="Select access level"
			searchPlaceholder="Search access levels"
			emptyText="No access level found."
			disabled={disabled}
			className={className}
			aria-label={ariaLabel ?? "Access level"}
			aria-describedby={describedBy}
			aria-invalid={invalid}
		/>
	);
}
