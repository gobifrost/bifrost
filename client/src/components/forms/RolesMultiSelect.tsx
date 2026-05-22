/**
 * Roles Multi-Select Component
 *
 * Searchable multi-select for picking N roles. Mirrors OrganizationSelect's
 * Popover + Command pattern, with checkmarks instead of a single selected
 * radio. Scales past long role lists (you can type to filter).
 *
 * Used by BulkReplaceRolesDialog. Other "pick roles" dialogs in the codebase
 * are migration candidates.
 */

import { useMemo, useState } from "react";
import { Check, ChevronsUpDown, Shield } from "lucide-react";

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
import { useRoles } from "@/hooks/useRoles";

export interface RolesMultiSelectProps {
	/** Currently selected role ids. */
	value: string[];
	/** Fired with the new selection on every change. */
	onChange: (next: string[]) => void;
	disabled?: boolean;
	placeholder?: string;
	triggerClassName?: string;
	contentClassName?: string;
}

export function RolesMultiSelect({
	value,
	onChange,
	disabled = false,
	placeholder = "Select roles...",
	triggerClassName,
	contentClassName,
}: RolesMultiSelectProps) {
	const [open, setOpen] = useState(false);
	const { data: roles, isLoading } = useRoles();

	const selectedSet = useMemo(() => new Set(value), [value]);

	const toggle = (id: string) => {
		const next = new Set(selectedSet);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		onChange(Array.from(next));
	};

	const summary =
		value.length === 0
			? placeholder
			: value.length === 1
				? roles?.find((r) => r.id === value[0])?.name ?? "1 selected"
				: `${value.length} selected`;

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					variant="outline"
					role="combobox"
					aria-expanded={open}
					aria-label="Select roles"
					className={cn(
						"w-full justify-between font-normal",
						triggerClassName,
					)}
					disabled={disabled || isLoading}
				>
					<span className="flex items-center gap-2 min-w-0">
						<Shield className="h-4 w-4 shrink-0 text-muted-foreground" />
						<span className="truncate">{summary}</span>
					</span>
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
					<CommandInput placeholder="Search roles..." />
					<CommandList className="max-h-72 overflow-y-auto">
						<CommandEmpty>No roles found.</CommandEmpty>
						<CommandGroup>
							{(roles ?? []).map((role) => {
								const selected = selectedSet.has(role.id);
								return (
									<CommandItem
										key={role.id}
										value={role.name}
										keywords={[role.description ?? ""].filter(Boolean)}
										onSelect={() => toggle(role.id)}
									>
										<Check
											className={cn(
												"mr-2 h-4 w-4",
												selected ? "opacity-100" : "opacity-0",
											)}
										/>
										<div className="flex flex-col min-w-0">
											<span className="truncate font-medium">
												{role.name}
											</span>
											{role.description && (
												<span className="truncate text-xs text-muted-foreground">
													{role.description}
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
