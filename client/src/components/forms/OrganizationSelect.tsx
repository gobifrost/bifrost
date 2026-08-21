/**
 * Organization Select Component
 *
 * A reusable searchable select for choosing an organization scope.
 * Platform admins can select "Global" (null) or any organization.
 * Org users should have this component hidden with their org pre-selected.
 */

import { useState } from "react";
import { Building2, ChevronsUpDown, Globe, Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
	CommandSeparator,
} from "@/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAdministrativeBoundary } from "@/hooks/useAdministrativeBoundary";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];

export interface OrganizationSelectProps {
	/** Selected organization ID, null for global scope, or undefined for all */
	value: string | null | undefined;
	/** Callback when selection changes */
	onChange: (value: string | null | undefined) => void;
	/** Whether the select is disabled */
	disabled?: boolean;
	/** Custom label for the field */
	label?: string;
	/** Whether to show the "Global" option (default true) */
	showGlobal?: boolean;
	/** Whether to show the "All organizations" option for filtering (default false) */
	showAll?: boolean;
	/** Placeholder text when nothing is selected */
	placeholder?: string;
	/** Custom className for the trigger button */
	triggerClassName?: string;
	/** Custom className for the popover content (useful for z-index overrides) */
	contentClassName?: string;
}

const GLOBAL_VALUE = "__GLOBAL__";
const ALL_VALUE = "__ALL__";

export function OrganizationSelect({
	value,
	onChange,
	disabled = false,
	showGlobal = true,
	showAll = false,
	placeholder = "Select organization...",
	triggerClassName,
	contentClassName,
}: OrganizationSelectProps) {
	const administrativeBoundary =
		useAdministrativeBoundary("organizations.read");
	const { data: organizations, isLoading } = useOrganizations({
		boundary: administrativeBoundary,
	});
	const [open, setOpen] = useState(false);

	const selectedOrg = organizations?.find(
		(org: Organization) => org.id === value,
	);

	const handleSelect = (selected: string) => {
		if (selected === ALL_VALUE) {
			onChange(undefined);
		} else if (selected === GLOBAL_VALUE) {
			onChange(null);
		} else {
			onChange(selected);
		}
		setOpen(false);
	};

	const renderTriggerContent = () => {
		if (isLoading) {
			return <span className="text-muted-foreground">Loading...</span>;
		}
		if (value === undefined && showAll) {
			return <span>All</span>;
		}
		if (value === null) {
			return (
				<div className="flex items-center gap-2">
					<Globe className="h-4 w-4 text-muted-foreground" />
					<span>Global</span>
				</div>
			);
		}
		if (selectedOrg) {
			return (
				<div className="flex items-center gap-2">
					{selectedOrg.is_provider ? (
						<Star className="h-4 w-4 text-amber-500 fill-amber-500" />
					) : (
						<Building2 className="h-4 w-4 text-muted-foreground" />
					)}
					<span>{selectedOrg.name}</span>
				</div>
			);
		}
		if (value) {
			return (
				<div className="flex items-center gap-2">
					<Building2 className="h-4 w-4 text-muted-foreground animate-pulse" />
					<span className="text-muted-foreground">Loading...</span>
				</div>
			);
		}
		return <span className="text-muted-foreground">{placeholder}</span>;
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="outline"
					role="combobox"
					aria-expanded={open}
					className={cn(
						"w-full justify-between font-normal",
						triggerClassName,
					)}
					disabled={disabled || isLoading}
				>
					{renderTriggerContent()}
					<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
				</Button>
			</PopoverTrigger>
			<PopoverContent
				className={cn(
					"w-[var(--radix-popover-trigger-width)] p-0",
					contentClassName,
				)}
				align="start"
			>
				<Command>
					<CommandInput placeholder="Search organizations..." />
					<CommandList className="max-h-60 overflow-y-auto">
						<CommandEmpty>No organizations found.</CommandEmpty>

						{showAll && (
							<>
								<CommandGroup>
									<CommandItem
										value={ALL_VALUE}
										keywords={["all"]}
										data-checked={value === undefined}
										onSelect={() => handleSelect(ALL_VALUE)}
									>
										<div className="flex flex-col flex-1">
											<span className="font-medium">All</span>
											<span className="text-xs text-muted-foreground">
												Show all organizations
											</span>
										</div>
									</CommandItem>
								</CommandGroup>
								<CommandSeparator />
							</>
						)}

						{showGlobal && (
							<>
								<CommandGroup>
									<CommandItem
										value={GLOBAL_VALUE}
										keywords={["global", "all organizations"]}
										data-checked={value === null}
										onSelect={() => handleSelect(GLOBAL_VALUE)}
									>
										<Globe className="mr-2 h-4 w-4 text-muted-foreground" />
										<div className="flex flex-col flex-1">
											<span className="font-medium">Global</span>
											<span className="text-xs text-muted-foreground">
												Available to all organizations
											</span>
										</div>
									</CommandItem>
								</CommandGroup>
								<CommandSeparator />
							</>
						)}

						<CommandGroup heading="Organizations">
							{organizations && organizations.length > 0 ? (
								organizations.map((org: Organization) => {
									const keywords = [org.name];
									if (org.domain) keywords.push(org.domain);
									if (org.is_provider) keywords.push("provider");
									return (
										<CommandItem
											key={org.id}
											value={org.id}
											keywords={keywords}
											data-checked={value === org.id}
											onSelect={() => handleSelect(org.id)}
										>
											{org.is_provider ? (
												<Star className="mr-2 h-4 w-4 text-amber-500 fill-amber-500" />
											) : (
												<Building2 className="mr-2 h-4 w-4 text-muted-foreground" />
											)}
											<div className="flex flex-col flex-1">
												<span className="flex items-center gap-2">
													{org.name}
													{org.is_provider && (
														<span className="text-xs text-amber-600 font-medium">
															Provider
														</span>
													)}
												</span>
												{org.domain && (
													<span className="text-xs text-muted-foreground">
														@{org.domain}
													</span>
												)}
											</div>
										</CommandItem>
									);
								})
							) : (
								<CommandItem disabled value="__none__">
									No organizations available
								</CommandItem>
							)}
						</CommandGroup>
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}
