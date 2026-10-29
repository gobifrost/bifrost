import * as React from "react";
import { ChevronsUpDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { SelectionChip } from "@/components/ui/selection-chip";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";

export interface MultiComboboxOption {
	value: string;
	label: string;
	description?: string;
}

export interface MultiComboboxProps {
	options: MultiComboboxOption[];
	value: string[];
	onValueChange: (values: string[]) => void;
	placeholder?: string;
	searchPlaceholder?: string;
	emptyText?: string;
	disabled?: boolean;
	isLoading?: boolean;
	className?: string;
	maxDisplayedItems?: number;
	/** Chips for the selected options above the trigger; off where the page lists the selection itself. */
	showSelected?: boolean;
}

export function MultiCombobox({
	options,
	value,
	onValueChange,
	placeholder = "Select options...",
	searchPlaceholder = "Search...",
	emptyText = "No option found.",
	disabled = false,
	isLoading = false,
	className,
	maxDisplayedItems,
	showSelected = true,
}: MultiComboboxProps) {
	const triggerRef = React.useRef<HTMLButtonElement>(null);
	const unavailable = disabled || isLoading;
	const [open, setOpen] = React.useState(false);

	const selectedOptions = options.filter((option) =>
		value.includes(option.value),
	);

	const displayedItems = maxDisplayedItems
		? selectedOptions.slice(0, maxDisplayedItems)
		: selectedOptions;
	const overflowCount = maxDisplayedItems
		? Math.max(0, selectedOptions.length - maxDisplayedItems)
		: 0;

	const handleToggle = (optionValue: string) => {
		if (unavailable) return;
		if (value.includes(optionValue)) {
			onValueChange(value.filter((v) => v !== optionValue));
		} else {
			onValueChange([...value, optionValue]);
		}
	};

	const handleRemove = (
		optionValue: string,
		e: React.MouseEvent | React.KeyboardEvent,
	) => {
		if (unavailable) return;
		e.stopPropagation();
		e.preventDefault();
		onValueChange(value.filter((v) => v !== optionValue));
		triggerRef.current?.focus();
	};

	return (
		<div className="min-w-0 space-y-2">
			{showSelected && displayedItems.length > 0 && (
				<ul
					aria-label="Selected options"
					className="flex min-w-0 flex-wrap gap-2"
				>
					{displayedItems.map((option) => (
						<li key={option.value} className="min-w-0 max-w-full">
							<SelectionChip
								label={option.label}
								disabled={unavailable}
								onRemove={(event) =>
									handleRemove(option.value, event)
								}
							/>
						</li>
					))}
					{overflowCount > 0 && (
						<li className="flex min-h-7 items-center text-xs text-muted-foreground">
							+{overflowCount} more
						</li>
					)}
				</ul>
			)}
			<Popover open={open && !unavailable} onOpenChange={setOpen}>
				<PopoverTrigger asChild>
					<Button
						ref={triggerRef}
						variant="outline"
						role="combobox"
						aria-expanded={open && !unavailable}
						aria-label={placeholder}
						className={cn(
							"w-full min-w-0 justify-between h-auto min-h-11 whitespace-normal text-left",
							className,
						)}
						disabled={unavailable}
					>
						<span>
							{isLoading
								? "Loading options…"
								: value.length
									? `${value.length} selected`
									: placeholder}
						</span>
						<ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
					</Button>
				</PopoverTrigger>
				<PopoverContent variant="picker" className="p-0" align="start">
					<Command>
						<CommandInput placeholder={searchPlaceholder} />
						<CommandList className="max-h-60 overflow-y-auto">
							<CommandEmpty>{emptyText}</CommandEmpty>
							<CommandGroup>
								{options.map((option) => (
									<CommandItem
										key={option.value}
										value={option.value}
										keywords={[
											option.label,
											option.description ?? "",
										]}
										disabled={unavailable}
										className="min-h-11 [overflow-wrap:anywhere]"
										data-checked={value.includes(
											option.value,
										)}
										onSelect={() =>
											handleToggle(option.value)
										}
									>
										<div className="flex min-w-0 flex-col flex-1">
											<span>{option.label}</span>
											{option.description && (
												<span className="text-xs text-muted-foreground">
													{option.description}
												</span>
											)}
										</div>
									</CommandItem>
								))}
							</CommandGroup>
						</CommandList>
					</Command>
				</PopoverContent>
			</Popover>
		</div>
	);
}
