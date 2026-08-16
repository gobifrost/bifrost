import * as React from "react";
import { ChevronsUpDown, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
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

export interface ComboboxOption {
	value: string;
	label: string;
	description?: string;
}

interface ComboboxProps {
	options: ComboboxOption[];
	value?: string;
	onValueChange?: (value: string) => void;
	placeholder?: string;
	searchPlaceholder?: string;
	emptyText?: string;
	disabled?: boolean;
	isLoading?: boolean;
	className?: string;
	id?: string;
}

export function Combobox({
	options,
	value,
	onValueChange,
	placeholder = "Select an option...",
	searchPlaceholder = "Search...",
	emptyText = "No option found.",
	disabled = false,
	isLoading = false,
	className,
	id,
}: ComboboxProps) {
	const [open, setOpen] = React.useState(false);
	const filter = React.useCallback(
		(value: string, search: string, keywords?: string[]) => {
			const terms = search.toLocaleLowerCase().trim().split(/\s+/);
			const searchable = [value, ...(keywords ?? [])]
				.join(" ")
				.toLocaleLowerCase();
			return terms.every((term) => searchable.includes(term)) ? 1 : 0;
		},
		[],
	);

	const selectedOption = options.find((option) => option.value === value);

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					id={id}
					variant="outline"
					role="combobox"
					aria-expanded={open}
					className={cn(
						"w-full justify-between font-normal",
						className,
					)}
					disabled={disabled || isLoading}
				>
					{isLoading ? (
						<>
							<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							<span className="text-muted-foreground">
								Loading...
							</span>
						</>
					) : (
						<>
							<span
								className={cn(
									"truncate",
									!selectedOption && "text-muted-foreground",
								)}
							>
								{selectedOption
									? selectedOption.label
									: placeholder}
							</span>
							<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
						</>
					)}
				</Button>
			</PopoverTrigger>
			<PopoverContent
				className="w-[var(--radix-popover-trigger-width)] p-0"
				align="start"
			>
				<Command filter={filter}>
					<CommandInput placeholder={searchPlaceholder} />
					<CommandList className="max-h-60 overflow-y-auto">
						<CommandEmpty>{emptyText}</CommandEmpty>
						<CommandGroup>
							{options.map((option) => (
								<CommandItem
									key={option.value}
									value={option.value}
									keywords={[option.label]}
									data-checked={value === option.value}
									onSelect={() => {
										onValueChange?.(
											option.value === value
												? ""
												: option.value,
										);
										setOpen(false);
									}}
								>
									<div className="flex flex-col flex-1">
										<span className="font-medium">
											{option.label}
										</span>
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
	);
}
