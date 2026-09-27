import { useMediaQuery } from "@/hooks/useMediaQuery";
import { CalendarIcon, X } from "lucide-react";
import { format } from "date-fns";
import type { DateRange } from "react-day-picker";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";

interface DateRangePickerProps {
	dateRange: DateRange | undefined;
	onDateRangeChange: (range: DateRange | undefined) => void;
	className?: string;
	maxDays?: number;
}

export function DateRangePicker({
	dateRange,
	onDateRangeChange,
	className,
	maxDays,
}: DateRangePickerProps) {
	const wideCalendar = useMediaQuery("(min-width: 640px)");
	const handleClear = (e: React.MouseEvent) => {
		e.stopPropagation();
		onDateRangeChange(undefined);
	};

	return (
		<div
			className={cn("flex w-full min-w-0 gap-2 sm:w-[300px]", className)}
		>
			<Popover>
				<PopoverTrigger asChild>
					<Button
						id="date"
						variant={"outline"}
						className={cn(
							"h-auto min-h-11 min-w-0 flex-1 justify-start whitespace-normal text-left font-normal sm:min-h-10",
							!dateRange && "text-muted-foreground",
						)}
					>
						<CalendarIcon className="mr-2 h-4 w-4" />
						<span className="min-w-0 [overflow-wrap:anywhere]">
							{dateRange?.from ? (
								dateRange.to ? (
									<>
										{format(dateRange.from, "LLL dd, y")} -{" "}
										{format(dateRange.to, "LLL dd, y")}
									</>
								) : (
									format(dateRange.from, "LLL dd, y")
								)
							) : (
								<span>Pick a date range</span>
							)}
						</span>
					</Button>
				</PopoverTrigger>
				<PopoverContent className="w-auto p-0" align="start">
					<Calendar
						mode="range"
						{...(dateRange?.from && {
							defaultMonth: dateRange.from,
						})}
						selected={dateRange}
						onSelect={onDateRangeChange}
						max={maxDays}
						numberOfMonths={wideCalendar ? 2 : 1}
					/>
				</PopoverContent>
			</Popover>
			{dateRange?.from && (
				<Button
					variant="ghost"
					size="icon"
					onClick={handleClear}
					className="size-11 shrink-0 sm:size-10"
					aria-label="Clear date filter"
					title="Clear date filter"
				>
					<X className="h-4 w-4" />
				</Button>
			)}
		</div>
	);
}
