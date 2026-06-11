/**
 * MCP Configuration Settings
 *
 * Configure external MCP (Model Context Protocol) access for Claude Desktop
 * and other MCP clients. Platform admins toggle the master enable switch and
 * manage which tools are exposed — per-user visibility is role-scoped via
 * agent access and handled outside this page.
 */

import { useState } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
} from "@/components/ui/command";
import { toast } from "sonner";
import {
	Loader2,
	CheckCircle2,
	AlertCircle,
	Plug,
	X,
	ChevronsUpDown,
	RotateCcw,
	Copy,
} from "lucide-react";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

type MCPToolInfo = components["schemas"]["MCPToolInfo"];

export function MCP() {
	// Form state
	const [enabled, setEnabled] = useState(true);
	const [allowedToolIds, setAllowedToolIds] = useState<string[] | null>(null);
	const [blockedToolIds, setBlockedToolIds] = useState<string[]>([]);

	// UI state
	const [saving, setSaving] = useState(false);
	const [hasChanges, setHasChanges] = useState(false);
	const [allowedToolsOpen, setAllowedToolsOpen] = useState(false);
	const [blockedToolsOpen, setBlockedToolsOpen] = useState(false);

	// MCP Server URL
	const mcpUrl = `${window.location.origin}/mcp`;

	const handleCopyMcpUrl = () => {
		navigator.clipboard.writeText(mcpUrl);
		toast.success("MCP URL copied to clipboard");
	};

	// Load current configuration
	const {
		data: config,
		isLoading: configLoading,
		refetch,
	} = $api.useQuery("get", "/api/mcp/config", undefined, {
		staleTime: 5 * 60 * 1000,
	});

	// Load available tools
	const { data: toolsData, isLoading: toolsLoading } = $api.useQuery(
		"get",
		"/api/mcp/tools",
		undefined,
		{
			staleTime: 5 * 60 * 1000,
		},
	);

	const tools: MCPToolInfo[] = toolsData?.tools || [];

	// Mutations
	const saveMutation = $api.useMutation("put", "/api/mcp/config");
	const deleteMutation = $api.useMutation("delete", "/api/mcp/config");

	// Update form when config loads. Adjust during render with a previous-
	// reference sentinel to avoid setState-in-effect.
	const [prevConfigRef, setPrevConfigRef] = useState<typeof config>(undefined);
	if (config && prevConfigRef !== config) {
		setPrevConfigRef(config);
		setEnabled(config.enabled);
		setAllowedToolIds(config.allowed_tool_ids ?? null);
		setBlockedToolIds(config.blocked_tool_ids ?? []);
		setHasChanges(false);
	}

	// Track changes
	const handleChange = () => {
		setHasChanges(true);
	};

	const handleSave = async () => {
		setSaving(true);
		try {
			await saveMutation.mutateAsync({
				body: {
					enabled,
					allowed_tool_ids: allowedToolIds,
					blocked_tool_ids: blockedToolIds,
				},
			});
			toast.success("MCP configuration saved");
			setHasChanges(false);
			refetch();
		} catch {
			toast.error("Failed to save MCP configuration");
		} finally {
			setSaving(false);
		}
	};

	const handleReset = async () => {
		setSaving(true);
		try {
			await deleteMutation.mutateAsync({});
			toast.success("MCP configuration reset to defaults");
			setHasChanges(false);
			refetch();
		} catch {
			toast.error("Failed to reset MCP configuration");
		} finally {
			setSaving(false);
		}
	};

	const toggleAllowedTool = (toolId: string) => {
		const current = allowedToolIds || [];
		if (current.includes(toolId)) {
			const newList = current.filter((id) => id !== toolId);
			setAllowedToolIds(newList.length > 0 ? newList : null);
		} else {
			setAllowedToolIds([...current, toolId]);
		}
		handleChange();
	};

	const toggleBlockedTool = (toolId: string) => {
		if (blockedToolIds.includes(toolId)) {
			setBlockedToolIds(blockedToolIds.filter((id) => id !== toolId));
		} else {
			setBlockedToolIds([...blockedToolIds, toolId]);
		}
		handleChange();
	};

	const removeAllowedTool = (toolId: string) => {
		const newList = (allowedToolIds || []).filter((id) => id !== toolId);
		setAllowedToolIds(newList.length > 0 ? newList : null);
		handleChange();
	};

	const removeBlockedTool = (toolId: string) => {
		setBlockedToolIds(blockedToolIds.filter((id) => id !== toolId));
		handleChange();
	};

	if (configLoading) {
		return (
			<div className="flex items-center justify-center h-64">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="space-y-6">
			{/* Status Banner */}
			{config?.is_configured ? (
				<div className="flex items-center gap-2 p-4 rounded-lg bg-green-500/10 text-green-600 dark:text-green-400">
					<CheckCircle2 className="h-5 w-5" />
					<div>
						<span className="font-medium">MCP Configured</span>
						{config.configured_at && (
							<span className="text-sm ml-2 opacity-75">
								Last updated{" "}
								{new Date(
									config.configured_at,
								).toLocaleDateString()}{" "}
								by {config.configured_by}
							</span>
						)}
					</div>
				</div>
			) : (
				<div className="flex items-center gap-2 p-4 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
					<AlertCircle className="h-5 w-5" />
					<span className="font-medium">
						Using Default Configuration
					</span>
				</div>
			)}

			{/* MCP Server URL */}
			<div className="rounded-lg bg-muted/50 p-3 ring-1 ring-foreground/5">
				<div className="flex items-center justify-between">
					<div>
						<p className="text-sm font-medium">MCP Server URL</p>
						<p className="text-xs text-muted-foreground mt-1 font-mono break-all">
							{mcpUrl}
						</p>
					</div>
					<Button
						variant="ghost"
						size="sm"
						onClick={handleCopyMcpUrl}
					>
						<Copy className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Main Configuration Card */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Plug className="h-5 w-5" />
						External MCP Access
					</CardTitle>
					<CardDescription>
						Allow Claude Desktop and other MCP clients to connect to
						Bifrost and use your workflows and tools.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{/* Enable/Disable Toggle */}
					<div className="flex items-center justify-between">
						<div className="space-y-0.5">
							<Label htmlFor="mcp-enabled" className="text-base">
								Enable MCP Access
							</Label>
							<p className="text-sm text-muted-foreground">
								When enabled, users can connect via MCP. Each
								user sees only the tools they're granted access
								to via agent roles.
							</p>
						</div>
						<Switch
							id="mcp-enabled"
							checked={enabled}
							onCheckedChange={(checked) => {
								setEnabled(checked);
								handleChange();
							}}
						/>
					</div>
				</CardContent>
			</Card>

			{/* Tool Access Card */}
			<Card>
				<CardHeader>
					<CardTitle>Tool Access Control</CardTitle>
					<CardDescription>
						Configure which tools are available via MCP. Leave empty
						to allow all tools.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{/* Allowed Tools */}
					<div className="space-y-3">
						<Label className="text-base">Allowed Tools</Label>
						<p className="text-sm text-muted-foreground">
							If set, only these tools will be available. Leave
							empty to allow all tools.
						</p>
						{(allowedToolIds || []).length > 0 && (
							<div className="flex flex-wrap gap-2">
								{(allowedToolIds || []).map((toolId) => {
									const tool = tools.find(
										(t) => t.id === toolId,
									);
									return (
										<Badge
											key={toolId}
											variant="secondary"
											className="gap-1"
										>
											{tool?.name || toolId}
											<button
												onClick={() =>
													removeAllowedTool(toolId)
												}
												className="ml-1 hover:bg-muted rounded-full"
											>
												<X className="h-3 w-3" />
											</button>
										</Badge>
									);
								})}
							</div>
						)}
						<Popover
							open={allowedToolsOpen}
							onOpenChange={setAllowedToolsOpen}
						>
							<PopoverTrigger asChild>
								<Button
									variant="outline"
									role="combobox"
									aria-expanded={allowedToolsOpen}
									className="w-full justify-between"
									disabled={toolsLoading}
								>
									{toolsLoading ? (
										<Loader2 className="h-4 w-4 animate-spin" />
									) : (
										"Select tools to allow..."
									)}
									<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
								</Button>
							</PopoverTrigger>
							<PopoverContent
								className="w-full p-0"
								align="start"
							>
								<Command>
									<CommandInput placeholder="Search tools..." />
									<CommandList>
										<CommandEmpty>
											No tools found.
										</CommandEmpty>
										<CommandGroup>
											{tools.map((tool) => (
												<CommandItem
													key={tool.id}
													value={tool.id}
													data-checked={(allowedToolIds || []).includes(tool.id)}
													onSelect={() =>
														toggleAllowedTool(
															tool.id,
														)
													}
												>
													<span className="font-mono text-sm">
														{tool.id}
													</span>
													<span className="text-xs text-muted-foreground ml-2">
														{tool.description}
													</span>
												</CommandItem>
											))}
										</CommandGroup>
									</CommandList>
								</Command>
							</PopoverContent>
						</Popover>
					</div>

					{/* Blocked Tools */}
					<div className="space-y-3">
						<Label className="text-base">Blocked Tools</Label>
						<p className="text-sm text-muted-foreground">
							These tools will never be available via MCP, even if
							in the allowed list.
						</p>
						{blockedToolIds.length > 0 && (
							<div className="flex flex-wrap gap-2">
								{blockedToolIds.map((toolId) => {
									const tool = tools.find(
										(t) => t.id === toolId,
									);
									return (
										<Badge
											key={toolId}
											variant="destructive"
											className="gap-1"
										>
											{tool?.name || toolId}
											<button
												onClick={() =>
													removeBlockedTool(toolId)
												}
												className="ml-1 hover:bg-destructive/80 rounded-full"
											>
												<X className="h-3 w-3" />
											</button>
										</Badge>
									);
								})}
							</div>
						)}
						<Popover
							open={blockedToolsOpen}
							onOpenChange={setBlockedToolsOpen}
						>
							<PopoverTrigger asChild>
								<Button
									variant="outline"
									role="combobox"
									aria-expanded={blockedToolsOpen}
									className="w-full justify-between"
									disabled={toolsLoading}
								>
									{toolsLoading ? (
										<Loader2 className="h-4 w-4 animate-spin" />
									) : (
										"Select tools to block..."
									)}
									<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
								</Button>
							</PopoverTrigger>
							<PopoverContent
								className="w-full p-0"
								align="start"
							>
								<Command>
									<CommandInput placeholder="Search tools..." />
									<CommandList>
										<CommandEmpty>
											No tools found.
										</CommandEmpty>
										<CommandGroup>
											{tools.map((tool) => (
												<CommandItem
													key={tool.id}
													value={tool.id}
													data-checked={blockedToolIds.includes(tool.id)}
													onSelect={() =>
														toggleBlockedTool(
															tool.id,
														)
													}
												>
													<span className="font-mono text-sm">
														{tool.id}
													</span>
													<span className="text-xs text-muted-foreground ml-2">
														{tool.description}
													</span>
												</CommandItem>
											))}
										</CommandGroup>
									</CommandList>
								</Command>
							</PopoverContent>
						</Popover>
					</div>
				</CardContent>
			</Card>

			{/* Action Buttons */}
			<div className="flex items-center gap-4">
				<Button onClick={handleSave} disabled={saving || !hasChanges}>
					{saving ? (
						<>
							<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							Saving...
						</>
					) : (
						"Save Configuration"
					)}
				</Button>
				<Button
					variant="outline"
					onClick={handleReset}
					disabled={saving || !config?.is_configured}
				>
					<RotateCcw className="mr-2 h-4 w-4" />
					Reset to Defaults
				</Button>
			</div>
		</div>
	);
}
