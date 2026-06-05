/**
 * MentionPicker Component
 *
 * Autocomplete popup for @mentioning agents in chat.
 * Shows a list of available agents when user types @.
 */

import { useEffect, useRef, useState, useMemo } from "react";
import { Bot, Check } from "lucide-react";
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
	PopoverAnchor,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { term, useTerminology } from "@/lib/terminology";
import { useAgents } from "@/hooks/useAgents";
import type { components } from "@/lib/v1";

type AgentSummary = components["schemas"]["AgentSummary"];

interface MentionPickerProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSelect: (agent: AgentSummary) => void;
	searchTerm: string;
	position?: { x: number; y: number };
}

export function MentionPicker({
	open,
	onOpenChange,
	onSelect,
	searchTerm,
	position,
}: MentionPickerProps) {
	const terminology = useTerminology();
	const { data: agents } = useAgents();
	const [selectedIndex, setSelectedIndex] = useState(0);
	const listRef = useRef<HTMLDivElement>(null);

	// Filter agents by search term - include searchTerm to reset selection
	const filteredAgents = useMemo(() => {
		// Reset selection when search term changes by returning fresh array
		const filtered =
			agents?.filter((agent) => {
				if (!searchTerm) return true;
				const term = searchTerm.toLowerCase();
				return (
					agent.name.toLowerCase().includes(term) ||
					agent.description?.toLowerCase().includes(term)
				);
			}) || [];
		return filtered;
	}, [agents, searchTerm]);

	// Clamp selectedIndex to valid range
	const clampedIndex = Math.min(
		selectedIndex,
		Math.max(0, filteredAgents.length - 1),
	);

	// Scroll selected item into view
	useEffect(() => {
		if (listRef.current && filteredAgents.length > 0) {
			const items = listRef.current.querySelectorAll("[cmdk-item]");
			const selectedItem = items[clampedIndex];
			if (selectedItem) {
				selectedItem.scrollIntoView({ block: "nearest" });
			}
		}
	}, [clampedIndex, filteredAgents.length]);

	// Handle keyboard navigation
	useEffect(() => {
		if (!open) return;

		const handleKeyDown = (e: KeyboardEvent) => {
			if (e.key === "ArrowDown") {
				e.preventDefault();
				setSelectedIndex((prev) =>
					prev < filteredAgents.length - 1 ? prev + 1 : prev,
				);
			} else if (e.key === "ArrowUp") {
				e.preventDefault();
				setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
			} else if (
				(e.key === "Enter" || e.key === "Tab") &&
				filteredAgents.length > 0
			) {
				e.preventDefault();
				onSelect(filteredAgents[clampedIndex]);
			} else if (e.key === "Escape") {
				e.preventDefault();
				onOpenChange(false);
			}
		};

		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [open, filteredAgents, clampedIndex, onSelect, onOpenChange]);

	if (!open) return null;

	return (
		<Popover open={open} onOpenChange={onOpenChange}>
			<PopoverAnchor
				style={{
					position: "absolute",
					left: position?.x ?? 0,
					top: position?.y ?? 0,
				}}
			/>
			<PopoverContent
				className="w-[300px] p-0"
				align="start"
				side="top"
				sideOffset={8}
				onOpenAutoFocus={(e) => e.preventDefault()}
			>
				<Command>
					<CommandInput
						placeholder={`Search ${term(terminology, "agent", "pluralLower")}...`}
						value={searchTerm}
						className="h-9"
					/>
					<CommandList ref={listRef}>
						<CommandEmpty>
							No {term(terminology, "agent", "pluralLower")} found.
						</CommandEmpty>
						<CommandGroup
							heading={term(terminology, "agent", "plural")}
						>
							{filteredAgents.map((agent, index) => (
								<CommandItem
									key={agent.id}
									value={agent.name}
									onSelect={() => onSelect(agent)}
									className={cn(
										"cursor-pointer",
										index === clampedIndex && "bg-accent",
									)}
								>
									<Bot className="mr-2 h-4 w-4 text-muted-foreground" />
									<div className="flex flex-col">
										<span>{agent.name}</span>
										{agent.description && (
											<span className="text-xs text-muted-foreground truncate max-w-[220px]">
												{agent.description}
											</span>
										)}
									</div>
									{index === clampedIndex && (
										<Check className="ml-auto h-4 w-4" />
									)}
								</CommandItem>
							))}
						</CommandGroup>
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}
