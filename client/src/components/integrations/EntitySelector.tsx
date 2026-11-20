/**
 * Entity Selector Component
 * Combobox-based searchable dropdown for selecting integration entities
 * Falls back to text input if no entities are available
 */

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { AlertCircle, RotateCw } from "lucide-react";
import type { DataProviderOption } from "@/services/dataProviders";
import { cn } from "@/lib/utils";

interface EntitySelectorProps {
	entities: DataProviderOption[];
	value: string;
	onChange: (value: string, label: string) => void;
	disabled?: boolean;
	placeholder?: string;
	isLoading?: boolean;
	isError?: boolean;
	onRetry?: () => void;
	"aria-label"?: string;
	className?: string;
}

export function EntitySelector({
	entities,
	value,
	onChange,
	disabled = false,
	placeholder = "Select entity...",
	isLoading = false,
	isError = false,
	onRetry,
	"aria-label": ariaLabel,
	className,
}: EntitySelectorProps) {
	// Show loading skeleton
	if (isLoading) {
		return (
			<Skeleton
				role="status"
				aria-label="Loading entities"
				className="h-9 w-full"
			/>
		);
	}

	// Show error state
	if (isError) {
		return (
			<div className="flex flex-wrap items-center gap-2">
				<div className="flex items-center gap-1 text-sm text-destructive flex-1">
					<AlertCircle className="h-4 w-4 shrink-0" />
					<span>Error loading entities</span>
				</div>
				{onRetry && (
					<Button
						variant="outline"
						size="sm"
						onClick={onRetry}
						className="min-h-11"
					>
						<RotateCw className="h-3 w-3 mr-1" />
						Retry
					</Button>
				)}
			</div>
		);
	}

	// Fall back to text input only when data provider actually failed
	if (!entities || entities.length === 0) {
		// All entities assigned to other orgs — show disabled combobox
		return (
			<Combobox
				options={[]}
				value={value}
				onValueChange={(selectedValue) =>
					onChange(selectedValue, selectedValue)
				}
				placeholder="All entities assigned"
				searchPlaceholder="Search entities..."
				emptyText="All entities are assigned to other organizations."
				disabled={disabled}
				aria-label={ariaLabel}
				className={cn("h-9 sm:h-9 text-sm", className)}
			/>
		);
	}

	// Convert DataProviderOption to ComboboxOption
	const options: ComboboxOption[] = entities.map((entity) => ({
		value: entity.value,
		label: entity.label,
		description: entity.description,
	}));

	return (
		<Combobox
			options={options}
			value={value}
			onValueChange={(selectedValue) => {
				// Find the selected option to get the label
				const selectedOption = options.find(
					(opt) => opt.value === selectedValue,
				);
				const label = selectedOption
					? selectedOption.label
					: selectedValue;
				onChange(selectedValue, label);
			}}
			placeholder={placeholder}
			searchPlaceholder="Search entities..."
			emptyText="No entities found."
			// Optional: choosing the mapped entity again unmaps it.
			allowClear
			disabled={disabled}
			aria-label={ariaLabel}
			className={cn("h-9 sm:h-9 text-sm", className)}
		/>
	);
}
