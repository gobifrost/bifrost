import * as React from "react";
import { ChevronsUpDown, Loader2, type LucideIcon } from "lucide-react";

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
	icon?: LucideIcon;
}

interface ComboboxProps {
	"aria-describedby"?: string;
	"aria-label"?: string;
	"aria-invalid"?: React.AriaAttributes["aria-invalid"];
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
	"aria-describedby": describedBy,
	"aria-label": ariaLabel,
	"aria-invalid": invalid,
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
	const SelectedIcon = selectedOption?.icon;

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					id={id}
					variant="outline"
					role="combobox"
					aria-label={ariaLabel}
					aria-expanded={open}
					aria-describedby={describedBy}
					aria-invalid={invalid}
					className={cn(
						"h-11 w-full min-w-0 justify-between overflow-hidden py-2 font-normal sm:h-10",
						className,
					)}
					disabled={disabled || isLoading}
				>
					{isLoading ? (
						<>
							<Loader2
								aria-hidden="true"
								className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
							/>
							<span className="text-muted-foreground">
								Loading...
							</span>
						</>
					) : (
						<>
							<span className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
								{SelectedIcon ? (
									<SelectedIcon
										aria-hidden="true"
										className="size-4 shrink-0 text-muted-foreground"
									/>
								) : null}
								<span
									className={cn(
										"min-w-0 truncate text-left",
										!value && "text-muted-foreground",
									)}
								>
									{selectedOption?.label ??
										(value || placeholder)}
								</span>
							</span>
							<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
						</>
					)}
				</Button>
			</PopoverTrigger>
			<PopoverContent variant="picker"
				className="max-h-[var(--radix-popover-content-available-height)] overflow-hidden p-0"
				align="start"
			>
				<Command
					filter={filter}
					label={searchPlaceholder}
					className="min-h-0"
				>
					<CommandInput
						aria-label={searchPlaceholder}
						placeholder={searchPlaceholder}
					/>
					<CommandList className="min-h-0 max-h-60 overflow-y-auto">
						<CommandEmpty>{emptyText}</CommandEmpty>
						<CommandGroup>
							{options.map((option) => {
								const Icon = option.icon;
								return (
									<CommandItem
										key={option.value}
										value={option.value}
										className="min-h-11"
										keywords={[
											option.label,
											option.description ?? "",
										]}
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
										{Icon ? (
											<Icon
												aria-hidden="true"
												className="size-4 shrink-0 text-muted-foreground"
											/>
										) : null}
										<div className="flex min-w-0 flex-1 flex-col [overflow-wrap:anywhere]">
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
								);
							})}
						</CommandGroup>
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}
