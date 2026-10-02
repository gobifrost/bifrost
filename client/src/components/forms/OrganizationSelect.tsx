/**
 * Organization Select Component
 *
 * A reusable searchable select for choosing an organization scope.
 * Platform admins can select "Global" (null) or any organization.
 * Org users should have this component hidden with their org pre-selected.
 */

import { useState, type ComponentProps } from "react";
import {
	Building2,
	ChevronsUpDown,
	Globe,
	Star,
	UserRound,
} from "lucide-react";
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
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];

export interface OrganizationSelectProps extends Pick<
	ComponentProps<typeof Button>,
	| "id"
	| "ref"
	| "onBlur"
	| "aria-label"
	| "aria-labelledby"
	| "aria-describedby"
	| "aria-invalid"
> {
	/** Selected organization ID, null for global scope, undefined for all, or PERSONAL_SCOPE when supported */
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
	/** Offer personal scope for resources that support ownership. */
	showPersonal?: boolean;
	/** Placeholder text when nothing is selected */
	placeholder?: string;
	/** Custom className for the trigger button */
	triggerClassName?: string;
	/** Custom className for the popover content (useful for z-index overrides) */
	contentClassName?: string;
	/** Offer only the organizations this returns true for */
	filterOrganizations?: (organization: Organization) => boolean;
}

export const PERSONAL_SCOPE = "__PERSONAL__";

const GLOBAL_VALUE = "__GLOBAL__";
const ALL_VALUE = "__ALL__";

export function OrganizationSelect({
	value,
	onChange,
	disabled = false,
	label,
	showGlobal = true,
	showAll = false,
	showPersonal = false,
	placeholder = "Select organization...",
	triggerClassName,
	contentClassName,
	filterOrganizations,
	...triggerProps
}: OrganizationSelectProps) {
	const {
		data: allOrganizations,
		isLoading,
		isFetching,
		error,
		refetch,
	} = useOrganizations();
	const organizations = filterOrganizations
		? allOrganizations?.filter(filterOrganizations)
		: allOrganizations;
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
		if (value === PERSONAL_SCOPE && showPersonal) {
			return (
				<span className="flex items-center gap-2">
					<UserRound className="size-4 text-muted-foreground" />
					Only me
				</span>
			);
		}
		if (value === undefined && showAll) {
			return <span>All</span>;
		}
		if (value === null) {
			return (
				<div className="flex min-w-0 items-center gap-2">
					<Globe className="h-4 w-4 text-muted-foreground" />
					<span>Global</span>
				</div>
			);
		}
		if (selectedOrg) {
			return (
				<div className="flex min-w-0 items-center gap-2">
					{selectedOrg.is_provider ? (
						<Star className="h-4 w-4 text-muted-foreground" />
					) : (
						<Building2 className="h-4 w-4 text-muted-foreground" />
					)}
					<span className="min-w-0 whitespace-normal text-left [overflow-wrap:anywhere]">
						{selectedOrg.name}
					</span>
				</div>
			);
		}
		if (value) {
			return (
				<div className="flex min-w-0 items-center gap-2">
					<Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
					<span className="min-w-0 whitespace-normal text-left text-muted-foreground [overflow-wrap:anywhere]">
						Organization unavailable
						<span className="block font-mono text-xs">{value}</span>
					</span>
				</div>
			);
		}
		return <span className="text-muted-foreground">{placeholder}</span>;
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					{...triggerProps}
					aria-label={
						triggerProps["aria-label"] ??
						label ??
						(triggerProps["aria-labelledby"]
							? undefined
							: "Organization scope")
					}
					variant="outline"
					role="combobox"
					aria-expanded={open}
					className={cn(
						"h-auto min-h-11 w-full min-w-0 justify-between py-2 font-normal lg:min-h-10",
						triggerClassName,
					)}
					disabled={disabled || isLoading}
				>
					{renderTriggerContent()}
					<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
				</Button>
			</PopoverTrigger>
			<PopoverContent
				variant="picker"
				className={cn("p-0", contentClassName)}
				align="start"
			>
				<Command>
					<CommandInput
						placeholder="Search organizations..."
						aria-label="Search organizations"
					/>
					{error && (
						<div
							role="alert"
							className="space-y-2 border-b p-3 text-sm"
						>
							<p>Organizations could not be loaded.</p>
							<Button
								variant="outline"
								className="min-h-11"
								disabled={isFetching}
								onClick={() => refetch()}
							>
								{isFetching
									? "Retrying…"
									: "Retry organizations"}
							</Button>
						</div>
					)}
					<CommandList className="max-h-60 overflow-y-auto">
						{!error && (
							<CommandEmpty>No organizations found.</CommandEmpty>
						)}

						{showPersonal && (
							<>
								<CommandGroup>
									<CommandItem
										value={PERSONAL_SCOPE}
										keywords={["personal", "only me"]}
										data-checked={value === PERSONAL_SCOPE}
										onSelect={() =>
											handleSelect(PERSONAL_SCOPE)
										}
									>
										<UserRound className="mr-2 size-4 text-muted-foreground" />
										<div className="flex min-w-0 flex-1 flex-col">
											<span className="font-medium">
												Only me
											</span>
											<span className="text-xs text-muted-foreground">
												Personal · visible only to you
											</span>
										</div>
									</CommandItem>
								</CommandGroup>
								<CommandSeparator />
							</>
						)}
						{showAll && (
							<>
								<CommandGroup>
									<CommandItem
										value={ALL_VALUE}
										keywords={["all"]}
										data-checked={value === undefined}
										onSelect={() => handleSelect(ALL_VALUE)}
									>
										<div className="flex min-w-0 flex-1 flex-col [overflow-wrap:anywhere]">
											<span className="font-medium">
												All
											</span>
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
										keywords={[
											"global",
											"all organizations",
										]}
										data-checked={value === null}
										onSelect={() =>
											handleSelect(GLOBAL_VALUE)
										}
									>
										<Globe className="mr-2 h-4 w-4 text-muted-foreground" />
										<div className="flex min-w-0 flex-1 flex-col [overflow-wrap:anywhere]">
											<span className="font-medium">
												Global
											</span>
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
									if (org.is_provider)
										keywords.push("provider");
									return (
										<CommandItem
											key={org.id}
											value={org.id}
											keywords={keywords}
											data-checked={value === org.id}
											onSelect={() =>
												handleSelect(org.id)
											}
										>
											{org.is_provider ? (
												<Star className="mr-2 h-4 w-4 text-muted-foreground" />
											) : (
												<Building2 className="mr-2 h-4 w-4 text-muted-foreground" />
											)}
											<div className="flex min-w-0 flex-1 flex-col [overflow-wrap:anywhere]">
												<span className="flex min-w-0 flex-wrap items-center gap-2">
													{org.name}
													{org.is_provider && (
														<span className="text-xs font-medium text-muted-foreground">
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
							) : !error ? (
								<CommandItem disabled value="__none__">
									No organizations available
								</CommandItem>
							) : null}
						</CommandGroup>
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}
