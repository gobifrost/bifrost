import { useState } from "react";
import {
	Filter,
	X,
	Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
	CommandSeparator,
} from "@/components/ui/command";
import { cn } from "@/lib/utils";
import type { Organization } from "./types";

export interface FilterPopoverProps {
	typeFilter: string;
	setTypeFilter: (v: string) => void;
	orgFilter: string;
	setOrgFilter: (v: string) => void;
	accessFilter: string;
	setAccessFilter: (v: string) => void;
	usageFilter: string;
	setUsageFilter: (v: string) => void;
	organizations: Organization[];
	activeFilterCount: number;
	onClearFilters: () => void;
}

export function FilterPopover({
	typeFilter,
	setTypeFilter,
	orgFilter,
	setOrgFilter,
	accessFilter,
	setAccessFilter,
	usageFilter,
	setUsageFilter,
	organizations,
	activeFilterCount,
	onClearFilters,
}: FilterPopoverProps) {
	const [open, setOpen] = useState(false);

	const typeOptions = [
		{ value: "all", label: "All Types" },
		{ value: "workflow", label: "Workflows" },
		{ value: "form", label: "Forms" },
		{ value: "agent", label: "Agents" },
		{ value: "app", label: "Apps" },
	];

	const orgOptions = [
		{ value: "all", label: "All Organizations" },
		{ value: "global", label: "Global" },
		...organizations.map((org) => ({ value: org.id, label: org.name })),
	];

	const accessOptions = [
		{ value: "all", label: "All Access Levels" },
		{ value: "authenticated", label: "Everyone except external users" },
		{ value: "everyone", label: "Everyone" },
		{ value: "role_based", label: "Role-based" },
	];

	const usageOptions = [
		{ value: "all", label: "All Usage" },
		{ value: "unused", label: "Unused (0 refs)" },
		{ value: "in_use", label: "In Use" },
	];

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button variant="outline" size="icon" className="h-9 w-9 relative">
					<Filter className="h-4 w-4" />
					{activeFilterCount > 0 && (
						<Badge
							variant="secondary"
							className="absolute -top-1 -right-1 h-4 w-4 p-0 flex items-center justify-center text-[10px]"
						>
							{activeFilterCount}
						</Badge>
					)}
				</Button>
			</PopoverTrigger>
			<PopoverContent className="w-80 p-0" align="start">
				<Command>
					<CommandInput placeholder="Search filters..." />
					<CommandList className="max-h-80">
						<CommandGroup heading="Entity Type">
							{typeOptions.map((option) => (
								<CommandItem
									key={option.value}
									value={option.label}
									onSelect={() => setTypeFilter(option.value)}
								>
									<Check
										className={cn(
											"mr-2 h-4 w-4",
											typeFilter === option.value
												? "opacity-100"
												: "opacity-0",
										)}
									/>
									{option.label}
								</CommandItem>
							))}
						</CommandGroup>
						<CommandSeparator />
						<CommandGroup heading="Organization">
							{orgOptions.map((option) => (
								<CommandItem
									key={option.value}
									value={option.label}
									onSelect={() => setOrgFilter(option.value)}
								>
									<Check
										className={cn(
											"mr-2 h-4 w-4",
											orgFilter === option.value
												? "opacity-100"
												: "opacity-0",
										)}
									/>
									{option.label}
								</CommandItem>
							))}
						</CommandGroup>
						<CommandSeparator />
						<CommandGroup heading="Access Level">
							{accessOptions.map((option) => (
								<CommandItem
									key={option.value}
									value={option.label}
									onSelect={() => setAccessFilter(option.value)}
								>
									<Check
										className={cn(
											"mr-2 h-4 w-4",
											accessFilter === option.value
												? "opacity-100"
												: "opacity-0",
										)}
									/>
									{option.label}
								</CommandItem>
							))}
						</CommandGroup>
						<CommandSeparator />
						<CommandGroup heading="Usage">
							{usageOptions.map((option) => (
								<CommandItem
									key={option.value}
									value={option.label}
									onSelect={() => setUsageFilter(option.value)}
								>
									<Check
										className={cn(
											"mr-2 h-4 w-4",
											usageFilter === option.value
												? "opacity-100"
												: "opacity-0",
										)}
									/>
									{option.label}
								</CommandItem>
							))}
						</CommandGroup>
						<CommandEmpty>No filters found.</CommandEmpty>
					</CommandList>
				</Command>
				{activeFilterCount > 0 && (
					<div className="p-2 border-t">
						<Button
							variant="ghost"
							size="sm"
							className="w-full"
							onClick={() => {
								onClearFilters();
								setOpen(false);
							}}
						>
							<X className="h-4 w-4 mr-2" />
							Clear all filters
						</Button>
					</div>
				)}
			</PopoverContent>
		</Popover>
	);
}
