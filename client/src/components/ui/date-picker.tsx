import { useState, useId, type ComponentProps } from "react";
import { CalendarIcon } from "lucide-react";
import { format, isValid, parseISO } from "date-fns";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type DatePickerProps = Omit<
	ComponentProps<typeof Button>,
	"value" | "onChange" | "children"
> & {
	/** A calendar date (YYYY-MM-DD), without a time or timezone. */
	value: string;
	onChange: (value: string) => void;
	placeholder?: string;
};

export function DatePicker({
	value,
	onChange,
	placeholder = "Pick a Date",
	disabled,
	className,
	...props
}: DatePickerProps) {
	const [open, setOpen] = useState(false);
	const valueDescriptionId = useId();
	const parsed = value ? parseISO(value) : undefined;
	const selected = parsed && isValid(parsed) ? parsed : undefined;

	const selectDate = (day: Date | undefined) => {
		if (disabled) return;
		onChange(day ? format(day, "yyyy-MM-dd") : "");
		setOpen(false);
	};

	return (
		<Popover open={open && !disabled} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					{...props}
					aria-describedby={[
						valueDescriptionId,
						props["aria-describedby"],
					]
						.filter(Boolean)
						.join(" ")}
					type="button"
					variant="outline"
					disabled={disabled}
					className={cn(
						"h-auto min-h-11 w-full min-w-0 justify-start whitespace-normal text-left font-normal",
						!value && "text-muted-foreground",
						className,
					)}
				>
					<CalendarIcon className="mr-2 size-4 shrink-0" />
					<span
						id={valueDescriptionId}
						className="min-w-0 [overflow-wrap:anywhere]"
					>
						{selected
							? format(selected, "MMM d, yyyy")
							: value || placeholder}
					</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent
				align="start"
				className="w-[min(20.5rem,calc(100vw-2rem))] sm:w-auto max-w-[calc(100vw-2rem)] max-h-(--radix-popover-content-available-height) gap-0 overflow-y-auto p-0"
			>
				<Calendar
					mode="single"
					required
					selected={selected}
					defaultMonth={selected}
					onSelect={selectDate}
					autoFocus
				/>
				<div className="border-t p-2">
					<Button
						type="button"
						variant="ghost"
						className="w-full"
						disabled={!value}
						onClick={() => selectDate(undefined)}
					>
						Clear Date
					</Button>
				</div>
			</PopoverContent>
		</Popover>
	);
}
