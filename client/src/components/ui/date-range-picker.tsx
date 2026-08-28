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
}

export function DateRangePicker({
	dateRange,
	onDateRangeChange,
	className,
}: DateRangePickerProps) {
	const handleClear = (e: React.MouseEvent) => {
		e.stopPropagation();
		onDateRangeChange(undefined);
	};

	return (
		<div className={cn("flex min-w-0 gap-2", className)}>
			<Popover>
				<PopoverTrigger asChild>
					<Button
						id="date"
						variant={"outline"}
						className={cn(
							"w-full justify-start text-left font-normal sm:w-[300px]",
							!dateRange && "text-muted-foreground",
						)}
					>
						<CalendarIcon className="mr-2 h-4 w-4" />
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
						numberOfMonths={2}
					/>
				</PopoverContent>
			</Popover>
			{dateRange?.from && (
				<Button
					variant="ghost"
					size="icon"
					onClick={handleClear}
					className="h-9 w-9"
					title="Clear date filter"
				>
					<X className="h-4 w-4" />
				</Button>
			)}
		</div>
	);
}
