import * as React from "react";
import { CalendarIcon } from "lucide-react";
import {
	format,
	isAfter,
	isBefore,
	setHours,
	setMinutes,
	setSeconds,
	setMilliseconds,
} from "date-fns";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";

export interface DateTimePickerProps {
	value: Date | null;
	onChange: (next: Date | null) => void;
	minDate?: Date;
	maxDate?: Date;
	disabled?: boolean;
	ariaLabel?: string;
}

const DISPLAY_FORMAT = "MMM d, yyyy · h:mm a";
const TIME_INPUT_FORMAT = "HH:mm";

/**
 * Round a Date up to the next 5-minute mark. Used as the default time
 * component when the user picks a day without first setting a time.
 */
function roundUpToNextFiveMinutes(date: Date): Date {
	const next = new Date(date);
	next.setSeconds(0, 0);
	const minutes = next.getMinutes();
	const add = (5 - (minutes % 5)) % 5;
	// If already on a 5-minute boundary, bump to the next one to guarantee
	// the result is in the future.
	next.setMinutes(minutes + (add === 0 ? 5 : add));
	return next;
}

/** Clamp a date into [minDate, maxDate] while preserving its HH:mm where legal. */
function clamp(date: Date, minDate?: Date, maxDate?: Date): Date {
	if (minDate && isBefore(date, minDate)) return new Date(minDate);
	if (maxDate && isAfter(date, maxDate)) return new Date(maxDate);
	return date;
}

function composeDateTime(
	day: Date,
	time: { hours: number; minutes: number },
): Date {
	let next = new Date(day);
	next = setHours(next, time.hours);
	next = setMinutes(next, time.minutes);
	next = setSeconds(next, 0);
	next = setMilliseconds(next, 0);
	return next;
}

export function DateTimePicker({
	value,
	onChange,
	minDate,
	maxDate,
	disabled,
	ariaLabel,
}: DateTimePickerProps): React.JSX.Element {
	const [open, setOpen] = React.useState(false);
	const timeInputId = React.useId();

	const label = ariaLabel ?? "Pick date and time";
	const triggerText = value
		? format(value, DISPLAY_FORMAT)
		: "Pick a date and time";

	const timeInputValue = value ? format(value, TIME_INPUT_FORMAT) : "";

	const handleDaySelect = (day: Date | undefined) => {
		if (!day || disabled) return;
		// Pull the time portion from current value or a sensible default.
		const source = value ?? roundUpToNextFiveMinutes(new Date());
		const composed = composeDateTime(day, {
			hours: source.getHours(),
			minutes: source.getMinutes(),
		});
		onChange(clamp(composed, minDate, maxDate));
	};

	const handleTimeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		if (disabled) return;
		const raw = e.target.value; // "HH:mm"
		if (!raw) return;
		const [hStr, mStr] = raw.split(":");
		const hours = Number(hStr);
		const minutes = Number(mStr);
		if (
			!Number.isFinite(hours) ||
			!Number.isFinite(minutes) ||
			hours < 0 ||
			hours > 23 ||
			minutes < 0 ||
			minutes > 59
		) {
			return;
		}
		const day = value ?? roundUpToNextFiveMinutes(new Date());
		const composed = composeDateTime(day, { hours, minutes });

		onChange(clamp(composed, minDate, maxDate));
	};

	// react-day-picker `disabled` matcher: block days outside the range.
	const disabledDays = React.useMemo(() => {
		const matchers: Array<(day: Date) => boolean> = [];
		if (minDate) {
			matchers.push((day) => isBefore(day, startOfDay(minDate)));
		}
		if (maxDate) {
			matchers.push((day) => isAfter(day, endOfDay(maxDate)));
		}
		if (matchers.length === 0) return undefined;
		return (day: Date) => matchers.some((m) => m(day));
	}, [minDate, maxDate]);

	return (
		<Popover open={open && !disabled} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					type="button"
					variant="outline"
					disabled={disabled}
					aria-label={label}
					className={cn(
						"h-auto min-h-11 w-full min-w-0 max-w-[260px] justify-start whitespace-normal text-left font-normal",
						!value && "text-muted-foreground",
					)}
				>
					<CalendarIcon className="mr-2 h-4 w-4" />
					<span className="min-w-0 [overflow-wrap:anywhere]">
						{triggerText}
					</span>
				</Button>
			</PopoverTrigger>
			<PopoverContent
				className="w-auto max-w-[calc(100vw-2rem)] max-h-(--radix-popover-content-available-height) gap-0 overflow-y-auto p-0"
				align="start"
			>
				<Calendar
					mode="single"
					selected={value ?? undefined}
					defaultMonth={value ?? minDate ?? undefined}
					onSelect={handleDaySelect}
					disabled={disabledDays}
				/>
				<div className="flex items-center gap-2 border-t p-3">
					<Label
						htmlFor={timeInputId}
						className="text-sm font-medium"
					>
						Time
					</Label>
					<Input
						id={timeInputId}
						type="time"
						step={60}
						value={timeInputValue}
						onChange={handleTimeChange}
						className="h-11 min-w-0 flex-1"
					/>
				</div>
			</PopoverContent>
		</Popover>
	);
}

function startOfDay(date: Date): Date {
	const d = new Date(date);
	d.setHours(0, 0, 0, 0);
	return d;
}

function endOfDay(date: Date): Date {
	const d = new Date(date);
	d.setHours(23, 59, 59, 999);
	return d;
}
