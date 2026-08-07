/**
 * WorkflowEditDialog Component
 *
 * Tabbed dialog for editing all workflow settings: general info, execution,
 * economics, tool/data provider config, access control, and HTTP endpoint.
 * Platform admin only.
 */

import { useEffect, useState } from "react";
import {
	Loader2,
	Check,
	ChevronsUpDown,
	X,
	Shield,
	Users,
	Settings,
	Timer,
	DollarSign,
	Bot,
	Database,
	Globe,
	Copy,
	RefreshCw,
} from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SolutionManagedBanner } from "@/components/solutions/SolutionManagedBanner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { TagsInput } from "@/components/ui/tags-input";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useRoles } from "@/hooks/useRoles";
import { useUpdateWorkflow } from "@/hooks/useWorkflows";
import {
	useWorkflowRoles,
	useAssignRolesToWorkflow,
	useRemoveRoleFromWorkflow,
} from "@/hooks/useWorkflowRoles";
import { useWorkflowKeys, useCreateWorkflowKey, useRevokeWorkflowKey } from "@/hooks/useWorkflowKeys";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import type { components } from "@/lib/v1";

type Workflow = components["schemas"]["WorkflowMetadata"];
type RolePublic = components["schemas"]["RolePublic"];

type WorkflowAccessLevel = "authenticated" | "everyone" | "role_based";

const ACCESS_LEVELS: {
	value: WorkflowAccessLevel;
	label: string;
	description: string;
	icon: React.ReactNode;
}[] = [
	{
		value: "authenticated",
		label: "Everyone except external users",
		description: "Any signed-in user except external users can execute",
		icon: <Users className="h-4 w-4" />,
	},
	{
		value: "everyone",
		label: "Everyone",
		description: "Any signed-in user, including external users, can execute",
		icon: <Users className="h-4 w-4" />,
	},
	{
		value: "role_based",
		label: "Role-Based",
		description: "Only users with assigned roles can execute",
		icon: <Shield className="h-4 w-4" />,
	},
];

const HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"] as const;

interface WorkflowEditDialogProps {
	workflow: Workflow | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSuccess?: () => void;
	initialTab?: string;
}

export function WorkflowEditDialog({
	workflow,
	open,
	onOpenChange,
	onSuccess,
	initialTab,
}: WorkflowEditDialogProps) {
	const { data: roles } = useRoles();
	const updateWorkflow = useUpdateWorkflow();
	const assignRoles = useAssignRolesToWorkflow();
	const removeRole = useRemoveRoleFromWorkflow();

	// Solution-managed workflows are read-only on the platform (criterion 6):
	// show the banner and disable Save. The API rejects the mutation regardless.
	const isSolutionManaged = workflow?.is_solution_managed ?? false;

	// Access control state
	const [organizationId, setOrganizationId] = useState<string | null | undefined>(undefined);
	const [accessLevel, setAccessLevel] = useState<WorkflowAccessLevel>("role_based");
	const [selectedRoleIds, setSelectedRoleIds] = useState<string[]>([]);
	const [rolesOpen, setRolesOpen] = useState(false);

	// General tab state
	const [workflowName, setWorkflowName] = useState("");
	const [displayName, setDisplayName] = useState("");
	const [description, setDescription] = useState("");
	const [category, setCategory] = useState("");
	const [tags, setTags] = useState<string[]>([]);

	// Execution tab state
	const [timeoutSeconds, setTimeoutSeconds] = useState(1800);

	// Economics tab state
	const [timeSaved, setTimeSaved] = useState(0);
	const [value, setValue] = useState(0);

	// Tool config state
	const [toolDescription, setToolDescription] = useState("");

	// Data provider config state
	const [cacheTtlSeconds, setCacheTtlSeconds] = useState(300);

	// Endpoint tab state
	const [endpointEnabled, setEndpointEnabled] = useState(false);
	const [allowedMethods, setAllowedMethods] = useState<string[]>(["POST"]);
	const [publicEndpoint, setPublicEndpoint] = useState(false);
	const [disableGlobalKey, setDisableGlobalKey] = useState(false);
	const [executionMode, setExecutionMode] = useState<"sync" | "async">("sync");
	const [newlyGeneratedKey, setNewlyGeneratedKey] = useState<string | null>(null);
	const [copiedCurl, setCopiedCurl] = useState(false);
	const [activeTab, setActiveTab] = useState("general");

	const [isSaving, setIsSaving] = useState(false);

	// API key management for endpoint tab
	const { data: existingKeys, refetch: refetchKeys } = useWorkflowKeys({
		workflowId: workflow?.id ?? undefined,
		includeRevoked: false,
	});
	const createKeyMutation = useCreateWorkflowKey();
	const revokeKeyMutation = useRevokeWorkflowKey();
	const workflowKey = existingKeys?.[0];
	const displayKey = newlyGeneratedKey || workflowKey?.masked_key || "";
	const hasKey = !!workflowKey || !!newlyGeneratedKey;

	// Fetch current workflow roles
	const workflowRolesQuery = useWorkflowRoles(workflow?.id);

	// Load workflow data when the dialog opens (or when the target workflow
	// changes while open). Adjusting state during render is the React-
	// recommended idiom for "reset state when an external value changes"
	// (https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes)
	// — avoids the extra effect+render cycle of useEffect+setState.
	const [prevLoadKey, setPrevLoadKey] = useState<string | null>(null);
	const loadKey = workflow && open ? `${workflow.id}:${initialTab ?? ""}` : null;
	if (prevLoadKey !== loadKey) {
		setPrevLoadKey(loadKey);
		if (workflow && open) {
			// Access control
			setOrganizationId(workflow.organization_id ?? null);
			setAccessLevel((workflow.access_level as WorkflowAccessLevel) || "role_based");

			// General
			setWorkflowName(workflow.name ?? "");
			setDisplayName(workflow.display_name ?? "");
			setDescription(workflow.description ?? "");
			setCategory(workflow.category ?? "General");
			setTags(workflow.tags ?? []);

			// Execution
			setTimeoutSeconds(workflow.timeout_seconds ?? 1800);

			// Economics
			setTimeSaved(workflow.time_saved ?? 0);
			setValue(workflow.value ?? 0);

			// Tool config
			setToolDescription(workflow.tool_description ?? "");

			// Data provider config
			setCacheTtlSeconds(workflow.cache_ttl_seconds ?? 300);

			// Endpoint
			setEndpointEnabled(workflow.endpoint_enabled ?? false);
			setAllowedMethods(workflow.allowed_methods ?? ["POST"]);
			setPublicEndpoint(workflow.public_endpoint ?? false);
			setDisableGlobalKey(workflow.disable_global_key ?? false);
			setExecutionMode(workflow.execution_mode ?? "sync");
			setNewlyGeneratedKey(null);
			setCopiedCurl(false);

			// Set initial tab
			setActiveTab(initialTab ?? "general");
		}
	}

	// Roles fetch is a side effect (network) and is the legitimate place for
	// useEffect — the resulting setState happens after an awaited query.
	useEffect(() => {
		if (workflow && open) {
			void workflowRolesQuery.refetch().then((result) => {
				if (result.data) {
					setSelectedRoleIds(result.data.role_ids || []);
				}
			});
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [workflow, open]);

	const handleClose = () => {
		onOpenChange(false);
	};

	const handleSave = async () => {
		if (!workflow?.id) return;

		setIsSaving(true);
		try {
			const resolvedWorkflowName =
				workflowName.trim() || workflow.function_name || workflow.name;

			// Build update payload with all changed fields
			await updateWorkflow.mutateAsync(workflow.id, {
				organization_id: organizationId,
				access_level: accessLevel,
				name: resolvedWorkflowName,
				display_name: displayName || null,
				description: description || null,
				category: category || "General",
				tags: tags,
				timeout_seconds: timeoutSeconds,
				execution_mode: executionMode,
				time_saved: timeSaved,
				value: value,
				tool_description: toolDescription || null,
				cache_ttl_seconds: cacheTtlSeconds,
				endpoint_enabled: endpointEnabled,
				allowed_methods: allowedMethods,
				public_endpoint: publicEndpoint,
				disable_global_key: disableGlobalKey,
			});

			// Handle role changes
			const currentRoleIds = workflowRolesQuery.data?.role_ids || [];
			const rolesToAdd = selectedRoleIds.filter(
				(id) => !currentRoleIds.includes(id)
			);
			const rolesToRemove = currentRoleIds.filter(
				(id) => !selectedRoleIds.includes(id)
			);

			if (rolesToAdd.length > 0) {
				await assignRoles.mutateAsync(workflow.id, rolesToAdd);
			}
			for (const roleId of rolesToRemove) {
				await removeRole.mutateAsync(workflow.id, roleId);
			}

			toast.success("Workflow updated", {
				description: `"${workflow.name}" has been updated successfully`,
			});

			onSuccess?.();
			handleClose();
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : "Failed to update workflow"
			);
		} finally {
			setIsSaving(false);
		}
	};

	const handleRoleToggle = (roleId: string) => {
		setSelectedRoleIds((prev) =>
			prev.includes(roleId)
				? prev.filter((id) => id !== roleId)
				: [...prev, roleId]
		);
	};

	const handleMethodToggle = (method: string) => {
		setAllowedMethods((prev) =>
			prev.includes(method)
				? prev.filter((m) => m !== method)
				: [...prev, method]
		);
	};

	const handleGenerateKey = async () => {
		if (!workflow?.id) return;
		try {
			// If already has a key, revoke it first
			if (workflowKey) {
				await revokeKeyMutation.mutateAsync(workflowKey.id);
			}
			const result = await createKeyMutation.mutateAsync({
				workflow_id: workflow.id,
				disable_global_key: false,
			});
			if (result.raw_key) {
				setNewlyGeneratedKey(result.raw_key);
				refetchKeys();
			}
		} catch {
			// Error handled by mutation hook
		}
	};

	const copyToClipboard = async (text: string) => {
		try {
			await navigator.clipboard.writeText(text);
			setCopiedCurl(true);
			setTimeout(() => setCopiedCurl(false), 2000);
		} catch {
			// Silently handle clipboard error
		}
	};

	// Determine which tabs to show based on workflow type
	const isToolType = workflow?.type === "tool";
	const isDataProviderType = workflow?.type === "data_provider";

	// Endpoint tab computed values
	const baseUrl = typeof window !== "undefined"
		? `${window.location.protocol}//${window.location.host}`
		: "";
	const endpointUrl = workflow?.id ? `${baseUrl}/api/endpoints/${workflow.id}` : "";
	const isPublicEndpoint = publicEndpoint;
	const apiKeyValue = displayKey || "YOUR_API_KEY";

	const exampleParams = workflow?.parameters?.reduce(
		(acc, param) => ({
			...acc,
			[param.name ?? "param"]:
				param.type === "string"
					? "<string>"
					: param.type === "int"
						? 0
						: param.type === "bool"
							? false
							: null,
		}),
		{} as Record<string, unknown>,
	) ?? {};

	const curlExample = isPublicEndpoint
		? `curl -X POST "${endpointUrl}" \\
  -H "Content-Type: application/json" \\
  -d '${JSON.stringify(exampleParams, null, 2)}'`
		: `curl -X POST "${endpointUrl}" \\
  -H "Content-Type: application/json" \\
  -H "X-Bifrost-Key: ${apiKeyValue}" \\
  -d '${JSON.stringify(exampleParams, null, 2)}'`;

	return (
		<Dialog open={open} onOpenChange={handleClose}>
			<DialogContent className="sm:max-w-[650px] max-h-[85vh] overflow-hidden flex flex-col">
				<DialogHeader>
					<DialogTitle>Edit Workflow Settings</DialogTitle>
					<DialogDescription>
						Configure settings for "{workflow?.name}"
					</DialogDescription>
				</DialogHeader>

				{isSolutionManaged && (
					<SolutionManagedBanner entityLabel="workflow" />
				)}

				<Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 overflow-hidden flex flex-col">
					<TabsList className="w-full flex-shrink-0">
						<TabsTrigger value="general" className="gap-1.5">
							<Settings className="h-3.5 w-3.5" />
							General
						</TabsTrigger>
						<TabsTrigger value="execution" className="gap-1.5">
							<Timer className="h-3.5 w-3.5" />
							Execution
						</TabsTrigger>
						<TabsTrigger value="economics" className="gap-1.5">
							<DollarSign className="h-3.5 w-3.5" />
							Economics
						</TabsTrigger>
						{isToolType && (
							<TabsTrigger value="tool" className="gap-1.5">
								<Bot className="h-3.5 w-3.5" />
								Tool
							</TabsTrigger>
						)}
						{isDataProviderType && (
							<TabsTrigger value="dataprovider" className="gap-1.5">
								<Database className="h-3.5 w-3.5" />
								Cache
							</TabsTrigger>
						)}
						<TabsTrigger value="access" className="gap-1.5">
							<Shield className="h-3.5 w-3.5" />
							Access
						</TabsTrigger>
						<TabsTrigger value="endpoint" className="gap-1.5">
							<Globe className="h-3.5 w-3.5" />
							Endpoint
						</TabsTrigger>
					</TabsList>

					<div className="flex-1 overflow-y-auto py-4">
						{/* General Tab */}
						<TabsContent value="general" className="mt-0 space-y-4">
							<div className="space-y-2">
								<Label htmlFor="workflow-name">Tool Name</Label>
								<Input
									id="workflow-name"
									value={workflowName}
									onChange={(e) => setWorkflowName(e.target.value)}
									placeholder={workflow?.function_name ?? "workflow_name"}
								/>
								<p className="text-xs text-muted-foreground">
									Used when this workflow is exposed as an MCP tool. Leave empty to reset to the Python function name.
								</p>
							</div>

							<div className="space-y-2">
								<Label htmlFor="display-name">Display Name</Label>
								<Input
									id="display-name"
									value={displayName}
									onChange={(e) => setDisplayName(e.target.value)}
									placeholder={workflow?.name ?? "Workflow name"}
								/>
								<p className="text-xs text-muted-foreground">
									Optional UI label. Leave empty to use the tool name.
								</p>
							</div>

							<div className="space-y-2">
								<Label htmlFor="description">Description</Label>
								<Textarea
									id="description"
									value={description}
									onChange={(e) => setDescription(e.target.value)}
									placeholder="Describe what this workflow does..."
									rows={3}
								/>
								<p className="text-xs text-muted-foreground">
									Initially set from code. Can be edited here or in manifest YAML.
								</p>
							</div>

							<div className="space-y-2">
								<Label htmlFor="category">Category</Label>
								<Input
									id="category"
									value={category}
									onChange={(e) => setCategory(e.target.value)}
									placeholder="General"
								/>
								<p className="text-xs text-muted-foreground">
									Initially set from code. Can be edited here or in manifest YAML.
								</p>
							</div>

							<div className="space-y-2">
								<Label>Tags</Label>
								<TagsInput
									value={tags}
									onChange={setTags}
									placeholder="Add tags..."
								/>
								<p className="text-xs text-muted-foreground">
									Press space, comma, or enter to add a tag
								</p>
							</div>
						</TabsContent>

						{/* Execution Tab */}
						<TabsContent value="execution" className="mt-0 space-y-4">
							<div className="space-y-2">
								<Label htmlFor="timeout">Timeout (seconds)</Label>
								<Input
									id="timeout"
									type="number"
									min={0}
									max={86400}
									value={timeoutSeconds}
									onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
								/>
								<p className="text-xs text-muted-foreground">
									Maximum execution time (0-86400 seconds, default 1800). Set to 0 to disable the timeout.
								</p>
							</div>
						</TabsContent>

						{/* Economics Tab */}
						<TabsContent value="economics" className="mt-0 space-y-4">
							<div className="space-y-2">
								<Label htmlFor="time-saved">Time Saved (minutes per execution)</Label>
								<Input
									id="time-saved"
									type="number"
									min={0}
									value={timeSaved}
									onChange={(e) => setTimeSaved(Number(e.target.value))}
								/>
								<p className="text-xs text-muted-foreground">
									Estimated minutes saved each time this workflow runs (for ROI reporting)
								</p>
							</div>

							<div className="space-y-2">
								<Label htmlFor="value">Value (per execution)</Label>
								<Input
									id="value"
									type="number"
									min={0}
									step={0.01}
									value={value}
									onChange={(e) => setValue(Number(e.target.value))}
								/>
								<p className="text-xs text-muted-foreground">
									Flexible value unit per execution (e.g., cost savings, revenue)
								</p>
							</div>
						</TabsContent>

						{/* Tool Config Tab (only for type='tool') */}
						{isToolType && (
							<TabsContent value="tool" className="mt-0 space-y-4">
								<div className="space-y-2">
									<Label htmlFor="tool-description">Tool Description</Label>
									<Textarea
										id="tool-description"
										value={toolDescription}
										onChange={(e) => setToolDescription(e.target.value)}
										placeholder="Describe what this tool does for AI agent selection..."
										rows={4}
									/>
									<p className="text-xs text-muted-foreground">
										Description optimized for AI tool selection. This helps agents
										decide when to use this tool.
									</p>
								</div>
							</TabsContent>
						)}

						{/* Data Provider Config Tab (only for type='data_provider') */}
						{isDataProviderType && (
							<TabsContent value="dataprovider" className="mt-0 space-y-4">
								<div className="space-y-2">
									<Label htmlFor="cache-ttl">Cache TTL (seconds)</Label>
									<Input
										id="cache-ttl"
										type="number"
										min={0}
										max={86400}
										value={cacheTtlSeconds}
										onChange={(e) => setCacheTtlSeconds(Number(e.target.value))}
									/>
									<p className="text-xs text-muted-foreground">
										How long to cache results (0-86400 seconds, default 300). Set to 0 to disable caching.
									</p>
								</div>
							</TabsContent>
						)}

						{/* Access Control Tab */}
						<TabsContent value="access" className="mt-0 space-y-4">
							<div className="space-y-2">
								<Label>Organization Scope</Label>
								<OrganizationSelect
									value={organizationId}
									onChange={setOrganizationId}
									showAll={false}
									showGlobal={true}
									placeholder="Select organization..."
								/>
								<p className="text-xs text-muted-foreground">
									Global workflows are available to all organizations
								</p>
							</div>

							<div className="space-y-2">
								<Label>Access Level</Label>
								<Select
									value={accessLevel}
									onValueChange={(v) =>
										setAccessLevel(v as WorkflowAccessLevel)
									}
								>
									<SelectTrigger>
										<SelectValue placeholder="Select access level" />
									</SelectTrigger>
									<SelectContent>
										{ACCESS_LEVELS.map((level) => (
											<SelectItem key={level.value} value={level.value}>
												<div className="flex items-center gap-2">
													{level.icon}
													<div className="flex flex-col">
														<span>{level.label}</span>
														<span className="text-xs text-muted-foreground">
															{level.description}
														</span>
													</div>
												</div>
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</div>

							{accessLevel === "role_based" && (
								<div className="space-y-2">
									<Label>
										Assigned Roles{" "}
										{selectedRoleIds.length > 0 && `(${selectedRoleIds.length})`}
									</Label>
									<Popover open={rolesOpen} onOpenChange={setRolesOpen}>
										<PopoverTrigger asChild>
											<Button
												variant="outline"
												role="combobox"
												aria-expanded={rolesOpen}
												className="w-full justify-between font-normal"
											>
												<span className="text-muted-foreground">
													Select roles...
												</span>
												<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
											</Button>
										</PopoverTrigger>
										<PopoverContent
											className="w-[var(--radix-popover-trigger-width)] p-0"
											align="start"
										>
											<Command>
												<CommandInput placeholder="Search roles..." />
												<CommandList>
													<CommandEmpty>No roles found.</CommandEmpty>
													<CommandGroup>
														{roles?.map((role: RolePublic) => (
															<CommandItem
																key={role.id}
																value={role.name || ""}
																data-checked={selectedRoleIds.includes(role.id)}
																onSelect={() => handleRoleToggle(role.id)}
															>
																<div className="flex flex-col flex-1">
																	<span className="font-medium">
																		{role.name}
																	</span>
																	{role.description && (
																		<span className="text-xs text-muted-foreground">
																			{role.description}
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

									{selectedRoleIds.length > 0 && (
										<div className="flex flex-wrap gap-2 rounded-md bg-muted/50 p-2 ring-1 ring-foreground/5">
											{selectedRoleIds.map((roleId) => {
												const role = roles?.find(
													(r: RolePublic) => r.id === roleId
												);
												return (
													<Badge
														key={roleId}
														variant="secondary"
														className="gap-1"
													>
														{role?.name || roleId}
														<X
															className="h-3 w-3 cursor-pointer"
															onClick={() => handleRoleToggle(roleId)}
														/>
													</Badge>
												);
											})}
										</div>
									)}

									<p className="text-xs text-muted-foreground">
										Users must have at least one of these roles to execute this
										workflow
									</p>

									{selectedRoleIds.length === 0 && (
										<p className="text-xs text-yellow-600 dark:text-yellow-500">
											No roles assigned - only platform admins can execute this
											workflow
										</p>
									)}
								</div>
							)}
						</TabsContent>

						{/* Endpoint Tab */}
						<TabsContent value="endpoint" className="mt-0 space-y-4">
							<div className="flex items-center justify-between">
								<div className="space-y-0.5">
									<Label>Enable HTTP Endpoint</Label>
									<p className="text-xs text-muted-foreground">
										Expose this workflow as an HTTP API endpoint
									</p>
								</div>
								<Switch
									checked={endpointEnabled}
									onCheckedChange={setEndpointEnabled}
								/>
							</div>

							{endpointEnabled && (
								<>
									{/* Execution Mode */}
									<div className="space-y-2">
										<Label>Execution Mode</Label>
										<Select
											value={executionMode}
											onValueChange={(v) => setExecutionMode(v as "sync" | "async")}
										>
											<SelectTrigger>
												<SelectValue />
											</SelectTrigger>
											<SelectContent>
												<SelectItem value="sync">
													<div className="flex flex-col">
														<span>Synchronous</span>
														<span className="text-xs text-muted-foreground">
															Wait for result before responding
														</span>
													</div>
												</SelectItem>
												<SelectItem value="async">
													<div className="flex flex-col">
														<span>Asynchronous</span>
														<span className="text-xs text-muted-foreground">
															Return immediately, poll for result
														</span>
													</div>
												</SelectItem>
											</SelectContent>
										</Select>
										<p className="text-xs text-muted-foreground">
											Controls whether HTTP endpoint calls wait for the result
										</p>
									</div>

									{/* Allowed Methods */}
									<div className="space-y-2">
										<Label>Allowed Methods</Label>
										<div className="flex flex-wrap gap-2">
											{HTTP_METHODS.map((method) => (
												<Button
													key={method}
													type="button"
													variant={
														allowedMethods.includes(method)
															? "default"
															: "outline"
													}
													size="sm"
													onClick={() => handleMethodToggle(method)}
												>
													{method}
												</Button>
											))}
										</div>
									</div>

									<div className="flex items-center justify-between">
										<div className="space-y-0.5">
											<Label>Public Endpoint</Label>
											<p className="text-xs text-muted-foreground">
												Skip authentication (use for incoming webhooks)
											</p>
										</div>
										<Switch
											checked={publicEndpoint}
											onCheckedChange={setPublicEndpoint}
										/>
									</div>

									<div className="flex items-center justify-between">
										<div className="space-y-0.5">
											<Label>Disable Global API Key</Label>
											<p className="text-xs text-muted-foreground">
												Only workflow-specific API keys will work
											</p>
										</div>
										<Switch
											checked={disableGlobalKey}
											onCheckedChange={setDisableGlobalKey}
										/>
									</div>

									{/* Endpoint URL */}
									<div className="space-y-2">
										<Label>Endpoint URL</Label>
										<Input
											value={endpointUrl}
											readOnly
											className="font-mono text-xs"
										/>
									</div>

									{/* API Key Management - Hidden for public endpoints */}
									{!isPublicEndpoint && (
										<div className="space-y-2">
											<Label>Workflow API Key</Label>
											{hasKey ? (
												<div className="flex items-center gap-2">
													<Input
														type="text"
														value={displayKey}
														readOnly
														className="font-mono text-xs flex-1"
													/>
													<Button
														variant="outline"
														size="sm"
														onClick={handleGenerateKey}
														disabled={createKeyMutation.isPending || revokeKeyMutation.isPending}
														title="Regenerate API key"
													>
														<RefreshCw className={cn("h-4 w-4", (createKeyMutation.isPending || revokeKeyMutation.isPending) && "animate-spin")} />
													</Button>
												</div>
											) : (
												<div className="flex items-center gap-2">
													<p className="text-sm text-muted-foreground flex-1">
														No API key configured
													</p>
													<Button
														variant="default"
														size="sm"
														onClick={handleGenerateKey}
														disabled={createKeyMutation.isPending}
													>
														{createKeyMutation.isPending ? (
															<>
																<RefreshCw className="mr-2 h-4 w-4 animate-spin" />
																Generating...
															</>
														) : (
															"Generate Key"
														)}
													</Button>
												</div>
											)}
											<p className="text-xs text-muted-foreground">
												{hasKey
													? "This key is specific to this workflow. Click refresh to regenerate."
													: "Generate a workflow-specific API key for authenticating HTTP requests."}
											</p>
										</div>
									)}

									{/* cURL Example */}
									<div className="space-y-2">
										<Label>Example Request</Label>
										<div className="relative">
											<pre className="rounded-md bg-muted p-4 text-xs overflow-x-auto ring-1 ring-foreground/5">
												<code>{curlExample}</code>
											</pre>
											<Button
												variant="ghost"
												size="sm"
												className="absolute top-2 right-2"
												onClick={() => copyToClipboard(curlExample)}
											>
												{copiedCurl ? (
													<Check className="h-3 w-3" />
												) : (
													<Copy className="h-3 w-3" />
												)}
											</Button>
										</div>
									</div>
								</>
							)}
						</TabsContent>
					</div>
				</Tabs>

				<DialogFooter>
					<Button variant="outline" onClick={handleClose} disabled={isSaving}>
						Cancel
					</Button>
					<Button onClick={handleSave} disabled={isSaving || isSolutionManaged}>
						{isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
						{isSaving ? "Saving..." : "Save Changes"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
