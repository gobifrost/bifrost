import type { ConfigSchemaItem } from "@/services/integrations";
import { Combobox } from "@/components/ui/combobox";

interface StaticConfigChoiceProps {
	id: string;
	options: string[];
	optionLabels?: ConfigSchemaItem["option_labels"];
	value: unknown;
	onChange: (value: string) => void;
	disabled?: boolean;
	"aria-describedby"?: string;
}

export function StaticConfigChoice({
	id,
	options,
	optionLabels,
	value,
	onChange,
	disabled,
	"aria-describedby": description,
}: StaticConfigChoiceProps) {
	const choices = options.map((option) => ({
		value: option,
		label: optionLabels?.[option] ?? option,
	}));
	if (typeof value === "string" && value !== "" && !options.includes(value)) {
		choices.unshift({ value, label: `${value} (Current Value)` });
	}
	return (
		<Combobox
			id={id}
			options={choices}
			value={typeof value === "string" ? value : ""}
			onValueChange={onChange}
			disabled={disabled}
			allowClear
			aria-describedby={description}
			placeholder="Not Set"
			searchPlaceholder="Search choices..."
		/>
	);
}
