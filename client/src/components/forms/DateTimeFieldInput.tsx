import { format, isValid, parseISO } from "date-fns";
import {
	DateTimePicker,
	type DateTimePickerProps,
} from "@/components/ui/date-time-picker";
import { cn } from "@/lib/utils";

type DateTimeFieldInputProps = Omit<
	DateTimePickerProps,
	"value" | "onChange" | "ariaLabel" | "clearable"
> & {
	value: string;
	onChange: (value: string) => void;
	"aria-label"?: string;
};

/** Forms retain local datetime strings; scheduling uses Date instants. */
export function DateTimeFieldInput({
	value,
	onChange,
	"aria-label": ariaLabel,
	className,
	placeholder = "Pick a Date and Time",
	...props
}: DateTimeFieldInputProps) {
	const parsed = value ? parseISO(value) : null;
	return (
		<DateTimePicker
			{...props}
			value={parsed && isValid(parsed) ? parsed : null}
			onChange={(next) =>
				onChange(next ? format(next, "yyyy-MM-dd'T'HH:mm") : "")
			}
			ariaLabel={ariaLabel}
			placeholder={placeholder}
			className={cn("max-w-none", className)}
			clearable
		/>
	);
}
