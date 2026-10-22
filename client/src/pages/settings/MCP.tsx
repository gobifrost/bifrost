import { MCPResetDialog } from "./MCPResetDialog";
import { MCPToolPicker } from "./MCPToolPicker";
import { MCPConnectionDetails } from "./MCPConnectionDetails";
import { SettingsReadError } from "./SettingsReadError";
/**
 * MCP Configuration Settings
 *
 * Configure external MCP (Model Context Protocol) access for Claude Desktop
 * and other MCP clients. Platform admins toggle the master enable switch and
 * manage which tools are exposed — per-user visibility is role-scoped via
 * agent access and handled outside this page.
 */

import { useRef, useState } from "react";
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
import { toast } from "sonner";
import { Loader2, Plug, RotateCcw } from "lucide-react";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

type MCPToolInfo = components["schemas"]["MCPToolInfo"];

export function MCP() {
	const headingRef = useRef<HTMLDivElement>(null);
	const [resetOpen, setResetOpen] = useState(false);
	const [resetComplete, setResetComplete] = useState(false);
	// Form state
	const [enabled, setEnabled] = useState(true);
	const [allowedToolIds, setAllowedToolIds] = useState<string[] | null>(null);
	const [blockedToolIds, setBlockedToolIds] = useState<string[]>([]);

	// UI state
	const [saving, setSaving] = useState(false);
	const [hasChanges, setHasChanges] = useState(false);

	// Load current configuration
	const {
		data: config,
		isLoading: configLoading,
		isError: configError,
		isFetching: configFetching,
		refetch,
	} = $api.useQuery("get", "/api/mcp/config", undefined, {
		staleTime: 5 * 60 * 1000,
	});

	// Load available tools
	const {
		data: toolsData,
		isLoading: toolsLoading,
		isError: toolsError,
		isFetching: toolsFetching,
		refetch: refetchTools,
	} = $api.useQuery("get", "/api/mcp/tools", undefined, {
		staleTime: 5 * 60 * 1000,
	});

	const tools: MCPToolInfo[] = toolsData?.tools || [];

	// Mutations
	const saveMutation = $api.useMutation("put", "/api/mcp/config");
	const deleteMutation = $api.useMutation("delete", "/api/mcp/config");

	// Update form when config loads. Adjust during render with a previous-
	// reference sentinel to avoid setState-in-effect.
	const [prevConfigRef, setPrevConfigRef] =
		useState<typeof config>(undefined);
	if (config && prevConfigRef !== config) {
		setPrevConfigRef(config);
		if (!hasChanges) {
			setEnabled(config.enabled);
			setAllowedToolIds(config.allowed_tool_ids ?? null);
			setBlockedToolIds(config.blocked_tool_ids ?? []);
			setHasChanges(false);
		}
	}

	// Track changes
	const handleChange = () => {
		setHasChanges(true);
	};

	const handleSave = async () => {
		if (saving || !hasChanges) return;
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
			await refetch();
		} catch {
			toast.error("Failed to save MCP configuration");
		} finally {
			setSaving(false);
		}
	};

	const handleReset = async () => {
		if (saving) return;
		setSaving(true);
		try {
			await deleteMutation.mutateAsync({});
			setResetComplete(true);
			setResetOpen(false);
			setEnabled(true);
			setAllowedToolIds(null);
			setBlockedToolIds([]);
			toast.success("MCP configuration reset to defaults");
			setHasChanges(false);
			await refetch();
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
			setAllowedToolIds(newList);
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
		setAllowedToolIds(newList);
		handleChange();
	};

	const handleAllowAllToolsChange = (allowAll: boolean) => {
		setAllowedToolIds(allowAll ? null : []);
		handleChange();
	};

	const removeBlockedTool = (toolId: string) => {
		setBlockedToolIds(blockedToolIds.filter((id) => id !== toolId));
		handleChange();
	};

	if (configLoading) {
		return (
			<div className="flex items-center justify-center h-64">
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	const readError = configError ? (
		<SettingsReadError
			resource="MCP configuration"
			cached={!!config}
			pending={configFetching}
			onRetry={() => {
				void refetch();
			}}
		/>
	) : null;
	if (!config) return readError;

	return (
		<div className="space-y-6">
			{readError}
			{toolsError && (
				<SettingsReadError
					resource="MCP tools"
					cached={!!toolsData}
					pending={toolsFetching}
					onRetry={() => {
						void refetchTools();
					}}
				/>
			)}
			<MCPConnectionDetails
				configured={config.is_configured}
				updatedAt={config.configured_at}
				updatedBy={config.configured_by}
				url={`${window.location.origin}/mcp`}
			/>

			{/* Main Configuration Card */}
			<Card>
				<CardHeader>
					<CardTitle
						ref={headingRef}
						tabIndex={-1}
						className="flex items-center gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					>
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
					<div className="flex items-start justify-between gap-4">
						<div className="space-y-0.5">
							<Label htmlFor="mcp-enabled" className="text-base">
								Enable MCP Access
							</Label>
							<p className="text-sm text-muted-foreground">
								When enabled, users can connect via MCP and
								discover only the agents and tools they can
								access.
							</p>
						</div>
						<Switch
							id="mcp-enabled"
							disabled={saving}
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
						Configure which tools are available via MCP. Allow all
						tools, or choose a specific allowed subset.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					<div className="flex items-start justify-between gap-4">
						<div className="space-y-0.5">
							<Label
								htmlFor="mcp-allow-all-tools"
								className="text-base"
							>
								Allow All Tools
							</Label>
							<p className="text-sm text-muted-foreground">
								When enabled, all tools are available unless
								they are blocked. When disabled, only the
								selected allowed tools are available; leaving
								that list empty blocks all tools.
							</p>
						</div>
						<Switch
							id="mcp-allow-all-tools"
							disabled={saving || toolsLoading || toolsError}
							checked={allowedToolIds === null}
							onCheckedChange={handleAllowAllToolsChange}
						/>
					</div>
					<MCPToolPicker
						title="Allowed Tools"
						description="Select tools to allow when Allow All Tools is disabled. Leaving this list empty blocks all tools."
						tools={tools}
						selected={allowedToolIds || []}
						disabled={
							saving ||
							toolsLoading ||
							toolsError ||
							allowedToolIds === null
						}
						onToggle={toggleAllowedTool}
						onRemove={removeAllowedTool}
					/>
					<MCPToolPicker
						title="Blocked Tools"
						description="These tools will never be available via MCP, even if in the allowed list."
						tools={tools}
						selected={blockedToolIds}
						disabled={saving || toolsLoading || toolsError}
						onToggle={toggleBlockedTool}
						onRemove={removeBlockedTool}
					/>
				</CardContent>
			</Card>

			{saveMutation.isError && (
				<p role="alert" className="text-sm text-destructive">
					Could not save MCP configuration. Your changes are still
					here. Try again.
				</p>
			)}
			{/* Action Buttons */}
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center [&>button]:min-h-11">
				<Button onClick={handleSave} disabled={saving || !hasChanges}>
					{saving ? (
						<>
							<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							Saving...
						</>
					) : (
						"Save Configuration"
					)}
				</Button>
				<Button
					variant="outline"
					onClick={() => {
						deleteMutation.reset();
						setResetComplete(false);
						setResetOpen(true);
					}}
					disabled={saving || !config?.is_configured}
				>
					<RotateCcw className="mr-2 h-4 w-4" />
					Reset to Defaults
				</Button>
			</div>
			<MCPResetDialog
				open={resetOpen}
				pending={saving}
				failed={deleteMutation.isError}
				completed={resetComplete}
				returnFocusRef={headingRef}
				onClose={() => setResetOpen(false)}
				onConfirm={() => {
					void handleReset();
				}}
			/>
		</div>
	);
}
