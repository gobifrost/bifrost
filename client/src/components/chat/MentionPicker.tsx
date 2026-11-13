/**
 * MentionPicker Component
 *
 * Autocomplete popup for @mentioning agents in chat.
 * Shows a list of available agents when user types @.
 */

import { useEffect, useRef, useState, useMemo } from "react";
import { Bot } from "lucide-react";
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
import { Button } from "@/components/ui/button";
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
	onSearchChange?: (value: string) => void;
	position?: { x: number; y: number };
}

export function MentionPicker({
	open,
	onOpenChange,
	onSelect,
	searchTerm,
	onSearchChange,
	position,
}: MentionPickerProps) {
	const terminology = useTerminology();
	const { data: agents, isLoading, isError, isFetching, refetch } = useAgents(undefined, { discoveryOnly: true });
	const selectionKey = `${open ? "open" : "closed"}:${searchTerm}`;
	const [selection, setSelection] = useState({ key: selectionKey, index: 0 });
	const listRef = useRef<HTMLDivElement>(null);
	if (selection.key !== selectionKey) {
		setSelection({ key: selectionKey, index: 0 });
	}
	const selectedIndex = selection.key === selectionKey ? selection.index : 0;

	const filteredAgents = useMemo(() => {
		const filtered =
			agents?.filter((agent) => {
				if (!agent.channels.includes("chat")) return false;
				if (!searchTerm) return true;
				const term = searchTerm.toLowerCase();
				return (
					agent.name.toLowerCase().includes(term) ||
					agent.description?.toLowerCase().includes(term)
				);
			}) || [];
		return filtered;
	}, [agents, searchTerm]);

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
			// The focused command input owns its navigation; do not select twice.
			if (e.target instanceof Element && e.target.closest("[cmdk-root], button")) return;
			if (e.key === "ArrowDown") {
				e.preventDefault();
				setSelection({
					key: selectionKey,
					index:
						clampedIndex < filteredAgents.length - 1
							? clampedIndex + 1
							: clampedIndex,
				});
			} else if (e.key === "ArrowUp") {
				e.preventDefault();
				setSelection({
					key: selectionKey,
					index: clampedIndex > 0 ? clampedIndex - 1 : clampedIndex,
				});
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
	}, [
		open,
		filteredAgents,
		clampedIndex,
		onSelect,
		onOpenChange,
		selectionKey,
	]);

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
				className="w-[300px] max-w-[calc(100vw-2rem)] p-0"
				align="start"
				side="top"
				sideOffset={8}
				onOpenAutoFocus={(e) => e.preventDefault()}
			>
				<Command
					shouldFilter={false}
					value={filteredAgents[clampedIndex]?.id ?? ""}
					onValueChange={(agentId) => {
						const index = filteredAgents.findIndex(
							(agent) => agent.id === agentId,
						);
						if (index >= 0) {
							setSelection({ key: selectionKey, index });
						}
					}}
				>
					<CommandInput
						placeholder={`Search ${term(terminology, "agent", "pluralLower")}...`}
						value={searchTerm}
						onValueChange={onSearchChange}
						aria-label={`Search ${term(terminology, "agent", "pluralLower")}`}
						className="h-11"
					/>
					{isLoading && <p role="status" className="p-4 text-sm text-muted-foreground">Loading {term(terminology, "agent", "pluralLower")}…</p>}
					{isError && (
						<div role="alert" className="space-y-2 border-b bg-[var(--bf-warning-soft)] p-4 text-sm">
							<p>{agents?.length ? "Could not refresh" : "Could not load"} {term(terminology, "agent", "pluralLower")}.</p>
							<Button variant="outline" className="min-h-11" disabled={isFetching} onClick={() => void refetch()}>{isFetching ? "Retrying…" : "Retry"}</Button>
						</div>
					)}
					<CommandList ref={listRef}>
						{!isLoading && !isError && <CommandEmpty>
							No {term(terminology, "agent", "pluralLower")}{" "}
							found.
						</CommandEmpty>}
						<CommandGroup
							heading={term(terminology, "agent", "plural")}
						>
							{filteredAgents.map((agent, index) => (
								<CommandItem
									key={agent.id}
									value={agent.id}
									data-checked={index === clampedIndex}
									onSelect={() => onSelect(agent)}
									className={cn(
										"min-h-11 cursor-pointer items-start",
										index === clampedIndex && "bg-muted",
									)}
								>
									<Bot className="mr-2 h-4 w-4 text-muted-foreground" />
									<div className="min-w-0 flex flex-col [overflow-wrap:anywhere]">
										<span>{agent.name}</span>
										{agent.description && (
											<span className="text-xs text-muted-foreground line-clamp-2">
												{agent.description}
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
