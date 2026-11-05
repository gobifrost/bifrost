/**
 * AgentSelectorDialog Component
 *
 * A dialog for selecting an agent with search, org badges, and descriptions.
 * Follows the same pattern as WorkflowSelectorDialog but simplified
 * (single-select only, no role mismatch logic).
 */

import { useCallback, useMemo, useState } from "react";
import { Building2, Globe, Loader2, Search } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useAgents } from "@/hooks/useAgents";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import type { components } from "@/lib/v1";

type AgentSummary = components["schemas"]["AgentSummary"];

export interface AgentSelectorDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	selectedAgentId: string | null;
	onSelect: (agentId: string) => void;
	title?: string;
	description?: string;
}

export function AgentSelectorDialog({
	open,
	onOpenChange,
	...props
}: AgentSelectorDialogProps) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-2xl">
				<AgentSelectorContent {...props} onOpenChange={onOpenChange} />
			</DialogContent>
		</Dialog>
	);
}

function AgentSelectorContent({
	onOpenChange,
	selectedAgentId,
	onSelect,
	title,
	description,
}: Omit<AgentSelectorDialogProps, "open">) {
	const [localSelection, setLocalSelection] = useState<string | null>(
		selectedAgentId,
	);
	const [searchQuery, setSearchQuery] = useState("");

	const handleOpenChange = onOpenChange;

	// Fetch agents and organizations
	const { data: agents, isLoading, error, refetch, isFetching } = useAgents();
	const { data: organizations } = useOrganizations({});

	const resolveOrganizationName = useOrganizationName(organizations);
	const getOrgName = useCallback(
		(orgId: string | null | undefined): string | null => {
			if (!orgId) return null;
			return resolveOrganizationName(orgId);
		},
		[resolveOrganizationName],
	);

	// Filter and sort
	const filteredAgents = useMemo(() => {
		let list = (agents ?? []).filter(
			(a): a is AgentSummary & { id: string } =>
				a.id != null && a.is_active,
		);

		if (searchQuery.trim()) {
			const query = searchQuery.toLowerCase();
			list = list.filter(
				(a) =>
					a.name.toLowerCase().includes(query) ||
					a.description?.toLowerCase().includes(query),
			);
		}

		// Sort: global first, then alphabetical
		return [...list].sort((a, b) => {
			const aIsGlobal = !a.organization_id;
			const bIsGlobal = !b.organization_id;
			if (aIsGlobal !== bIsGlobal) return aIsGlobal ? -1 : 1;
			return a.name.localeCompare(b.name);
		});
	}, [agents, searchQuery]);

	const handleConfirm = useCallback(() => {
		if (
			localSelection &&
			!error &&
			agents?.some(
				(agent) => agent.id === localSelection && agent.is_active,
			)
		) {
			onSelect(localSelection);
		}
		handleOpenChange(false);
	}, [localSelection, error, agents, onSelect, handleOpenChange]);

	return (
		<>
			<DialogHeader>
				<DialogTitle>{title || "Select Agent"}</DialogTitle>
				<DialogDescription>
					{description ||
						"Choose an agent to receive events from this source."}
				</DialogDescription>
			</DialogHeader>

			<div className="flex-1 min-h-0 flex flex-col rounded-[var(--bf-radius-surface)] border border-border overflow-hidden">
				{/* Search */}
				<div className="p-3 border-b bg-muted/20">
					<div className="relative">
						<Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
						<Input
							aria-label="Search agents"
							placeholder="Search agents..."
							value={searchQuery}
							onChange={(e) => setSearchQuery(e.target.value)}
							className="min-h-11 pl-9"
						/>
					</div>
				</div>

				{/* Agent list */}
				<div className="max-h-[45dvh] overflow-y-auto p-2">
					{isLoading ? (
						<div className="flex items-center justify-center py-8">
							<Loader2 className="h-6 w-6 motion-safe:animate-spin text-muted-foreground" />
							<span className="ml-2 text-sm text-muted-foreground">
								Loading agents...
							</span>
						</div>
					) : error ? (
						<Alert variant="destructive">
							<AlertTitle>Failed to load agents</AlertTitle>
							<AlertDescription>
								<Button
									type="button"
									variant="outline"
									className="mt-2 min-h-11"
									disabled={isFetching}
									onClick={() => {
										void refetch();
									}}
								>
									Retry
								</Button>
							</AlertDescription>
						</Alert>
					) : filteredAgents.length === 0 ? (
						<div className="flex items-center justify-center py-8 text-muted-foreground">
							<span className="text-sm">
								{searchQuery
									? "No agents match your search"
									: "No agents available"}
							</span>
						</div>
					) : (
						<div className="space-y-1">
							{filteredAgents.map((agent) => (
								<AgentListItem
									key={agent.id}
									agent={agent}
									isSelected={localSelection === agent.id}
									onToggle={() => setLocalSelection(agent.id)}
									orgName={getOrgName(agent.organization_id)}
								/>
							))}
						</div>
					)}
				</div>
			</div>

			<DialogFooter>
				<Button
					type="button"
					className="min-h-11"
					variant="outline"
					onClick={() => handleOpenChange(false)}
				>
					Cancel
				</Button>
				<Button
					type="button"
					className="min-h-11"
					onClick={handleConfirm}
					disabled={
						isLoading ||
						!!error ||
						!agents?.some(
							(agent) =>
								agent.id === localSelection && agent.is_active,
						)
					}
				>
					Select
				</Button>
			</DialogFooter>
		</>
	);
}

function AgentListItem({
	agent,
	isSelected,
	onToggle,
	orgName,
}: {
	agent: AgentSummary & { id: string };
	isSelected: boolean;
	onToggle: () => void;
	orgName: string | null;
}) {
	return (
		<button
			type="button"
			aria-pressed={isSelected}
			onClick={onToggle}
			className={cn(
				"min-h-11 min-w-0 w-full text-left p-3 rounded-[var(--bf-radius-control)] border transition-colors motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
				"hover:bg-accent/50",
				isSelected
					? "border-primary bg-primary/5"
					: "border-transparent bg-transparent",
			)}
		>
			<div className="flex items-start gap-3">
				{/* Radio indicator */}
				<div className="flex-shrink-0 mt-0.5">
					<div
						className={cn(
							"h-4 w-4 rounded-full border-2 flex items-center justify-center",
							isSelected
								? "border-primary bg-primary"
								: "border-muted-foreground",
						)}
					>
						{isSelected && (
							<div className="h-1.5 w-1.5 rounded-full bg-primary-foreground" />
						)}
					</div>
				</div>

				{/* Agent info */}
				<div className="flex-1 min-w-0">
					<div className="flex items-center gap-2 flex-wrap">
						<span className="min-w-0 font-medium [overflow-wrap:anywhere]">
							{agent.name}
						</span>
						{orgName ? (
							<Badge
								variant="outline"
								className="text-xs px-1.5 py-1 h-auto max-w-full whitespace-normal [overflow-wrap:anywhere] text-muted-foreground"
							>
								<Building2 className="h-3 w-3 mr-1 shrink-0" />
								{orgName}
							</Badge>
						) : (
							<Badge
								variant="default"
								className="text-xs px-1.5 py-0 h-5"
							>
								<Globe className="h-3 w-3 mr-1 shrink-0" />
								Global
							</Badge>
						)}
					</div>
					<p
						className={cn(
							"text-sm text-muted-foreground mt-0.5 [overflow-wrap:anywhere]",
							!agent.description && "italic",
						)}
					>
						{agent.description || "No description"}
					</p>
				</div>
			</div>
		</button>
	);
}
