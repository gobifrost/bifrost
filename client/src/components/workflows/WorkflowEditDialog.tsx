/**
 * WorkflowEditDialog Component
 *
 * Tabbed dialog for editing all workflow settings: general info, execution,
 * economics, tool/data provider config, access control, and HTTP endpoint.
 * Platform admin only.
 */

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import {
	Loader2,
	Check,
	ChevronsUpDown,
	X,
	Shield,
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
import { copyToClipboard } from "@/lib/clipboard";
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
import { AccessLevelSelect } from "@/components/access/AccessLevelSelect";
import { WorkflowAccessPanel } from "@/components/workflows/WorkflowAccessPanel";
import type { components } from "@/lib/v1";

type Workflow = components["schemas"]["WorkflowMetadata"];
type RolePublic = components["schemas"]["RolePublic"];

type WorkflowAccessLevel = "authenticated" | "everyone" | "role_based";

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
	const prefersReducedMotion = useReducedMotion();
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
	const [rolesReady, setRolesReady] = useState(false);
	const [rolesLoadError, setRolesLoadError] = useState(false);
	const [rolesLoadAttempt, setRolesLoadAttempt] = useState(0);
	const loadedRoleIds = useRef<string[]>([]);

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
	const [keyBusy, setKeyBusy] = useState(false);
	const keyBusyRef = useRef(false);
	const [keyRevoked, setKeyRevoked] = useState(false);
	const [keyError, setKeyError] = useState<string | null>(null);
	const keyErrorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (keyError) {
			keyErrorRef.current?.focus();
			keyErrorRef.current?.scrollIntoView?.({ block: "nearest" });
		}
	}, [keyError]);
	const [curlCopyState, setCurlCopyState] = useState<
		"idle" | "copying" | "copied" | "error"
	>("idle");
	const [activeTab, setActiveTab] = useState("general");

	const [isSaving, setIsSaving] = useState(false);
	const savingRef = useRef(false);
	const [saveError, setSaveError] = useState<string | null>(null);
	const saveErrorRef = useRef<HTMLParagraphElement>(null);
	useEffect(() => {
		if (saveError) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [saveError]);
	const copyResetTimerRef = useRef<number | null>(null);
	const copyMountedRef = useRef(true);
	const copyErrorRef = useRef<HTMLParagraphElement>(null);
	useEffect(() => {
		if (curlCopyState === "error") copyErrorRef.current?.focus();
	}, [curlCopyState]);

	useEffect(() => {
		copyMountedRef.current = true;
		return () => {
			copyMountedRef.current = false;
			if (copyResetTimerRef.current !== null) {
				window.clearTimeout(copyResetTimerRef.current);
			}
		};
	}, []);

	// API key management for endpoint tab
	const { data: existingKeys, refetch: refetchKeys, isLoading: keysLoading, isError: keysError, isFetching: keysFetching } = useWorkflowKeys({
		workflowId: workflow?.id ?? undefined,
		includeRevoked: false,
	});
	const createKeyMutation = useCreateWorkflowKey({ errorToast: false });
	const revokeKeyMutation = useRevokeWorkflowKey({ errorToast: false, successToast: false });
	const workflowKey = existingKeys?.[0];
	const displayKey = newlyGeneratedKey || (!keyRevoked && workflowKey?.masked_key) || "";
	const hasKey = (!keyRevoked && !!workflowKey) || !!newlyGeneratedKey;

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
			setSelectedRoleIds([]);
			setRolesReady(false);
			setRolesLoadError(false);
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
			setKeyRevoked(false);
			setKeyError(null);
			setCurlCopyState("idle");
			setSaveError(null);

			// Set initial tab
			setActiveTab(initialTab ?? "general");
		}
	}

	// Ignore late responses after closing or switching workflows. Parent list
	// refreshes must not replace a role selection the user is still editing.
	const workflowId = workflow?.id;
	useEffect(() => {
		if (!workflowId || !open) return;
		let cancelled = false;
		void workflowRolesQuery.refetch().then((result) => {
			if (cancelled) return;
			if (!result.data) {
				setRolesLoadError(true);
				return;
			}
			loadedRoleIds.current = result.data.role_ids || [];
			setSelectedRoleIds(loadedRoleIds.current);
			setRolesReady(true);
		}).catch(() => {
			if (!cancelled) setRolesLoadError(true);
		});
		return () => { cancelled = true; };
		// The manual query wrapper is recreated each render; identity is the ID.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [workflowId, open, initialTab, rolesLoadAttempt]);

	const handleClose = () => {
		if (savingRef.current || keyBusyRef.current) return;
		onOpenChange(false);
	};

	const handleSave = async () => {
		if (!workflow?.id || savingRef.current || keyBusyRef.current || !rolesReady) return;

		savingRef.current = true;
		setSaveError(null);
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
			const currentRoleIds = loadedRoleIds.current;
			const rolesToAdd = selectedRoleIds.filter(
				(id) => !currentRoleIds.includes(id)
			);
			const rolesToRemove = currentRoleIds.filter(
				(id) => !selectedRoleIds.includes(id)
			);

			if (rolesToAdd.length > 0) {
				await assignRoles.mutateAsync(workflow.id, rolesToAdd);
				loadedRoleIds.current = [...loadedRoleIds.current, ...rolesToAdd];
			}
			for (const roleId of rolesToRemove) {
				await removeRole.mutateAsync(workflow.id, roleId);
				loadedRoleIds.current = loadedRoleIds.current.filter((id) => id !== roleId);
			}

			toast.success("Workflow updated", {
				description: `"${workflow.name}" has been updated successfully`,
			});

			onSuccess?.();
			onOpenChange(false);
		} catch (error) {
			setSaveError(
				error instanceof Error ? error.message : "Failed to update workflow"
			);
		} finally {
			savingRef.current = false;
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
		if (!workflow?.id || keyBusyRef.current || savingRef.current || keysLoading || keysError || keysFetching) return;
		keyBusyRef.current = true;
		setKeyBusy(true);
		setKeyError(null);
		let revoked = keyRevoked;
		try {
			if ((workflowKey || newlyGeneratedKey) && !revoked) {
				await revokeKeyMutation.mutateAsync(workflow.id);
				revoked = true;
				setKeyRevoked(true);
				setNewlyGeneratedKey(null);
			}
			const result = await createKeyMutation.mutateAsync({
				workflow_id: workflow.id,
				disable_global_key: false,
			});
			if (result.raw_key) {
				setNewlyGeneratedKey(result.raw_key);
				setKeyRevoked(false);
				void refetchKeys();
			}
		} catch {
			setKeyError(revoked
				? "The previous API key was revoked, but the replacement could not be created. Generate a new key to restore API access."
				: "Could not generate the API key. Try again.");
		} finally {
			keyBusyRef.current = false;
			setKeyBusy(false);
		}
	};

	const copyCurlExample = async () => {
		if (curlCopyState === "copying") return;
		setCurlCopyState("copying");
		if (copyResetTimerRef.current !== null) {
			window.clearTimeout(copyResetTimerRef.current);
			copyResetTimerRef.current = null;
		}
		const copied = await copyToClipboard(curlExample);
		if (!copyMountedRef.current) return;
		if (copied) {
			setCurlCopyState("copied");
			if (copyResetTimerRef.current !== null) {
				window.clearTimeout(copyResetTimerRef.current);
			}
			copyResetTimerRef.current = window.setTimeout(() => {
				setCurlCopyState("idle");
				copyResetTimerRef.current = null;
			}, 2000);
			return;
		}
		setCurlCopyState("error");
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
			<DialogContent className="max-h-[min(90dvh,54rem)] w-[min(calc(100vw-1rem),52rem)] overflow-hidden flex flex-col sm:max-w-none">
				<DialogHeader>
					<DialogTitle>Edit Workflow Settings</DialogTitle>
					<DialogDescription>
						Configure settings for "{workflow?.name}"
					</DialogDescription>
				</DialogHeader>

				{isSolutionManaged && (
					<SolutionManagedBanner entityLabel="workflow" />
				)}

				<Tabs inert={isSaving || keyBusy} aria-busy={isSaving || keyBusy} value={activeTab} onValueChange={setActiveTab} className="flex-1 overflow-hidden flex flex-col">
					<TabsList className="w-full min-h-14 shrink-0 flex-nowrap justify-start gap-1 overflow-x-auto overflow-y-hidden group-data-horizontal/tabs:h-auto">
						<TabsTrigger value="general" className="h-11 min-h-11 flex-none gap-1.5">
							<Settings className="h-3.5 w-3.5" />
							General
						</TabsTrigger>
						<TabsTrigger value="execution" className="h-11 min-h-11 flex-none gap-1.5">
							<Timer className="h-3.5 w-3.5" />
							Execution
						</TabsTrigger>
						<TabsTrigger value="economics" className="h-11 min-h-11 flex-none gap-1.5">
							<DollarSign className="h-3.5 w-3.5" />
							Economics
						</TabsTrigger>
						{isToolType && (
							<TabsTrigger value="tool" className="h-11 min-h-11 flex-none gap-1.5">
								<Bot className="h-3.5 w-3.5" />
								Tool
							</TabsTrigger>
						)}
						{isDataProviderType && (
							<TabsTrigger value="dataprovider" className="h-11 min-h-11 flex-none gap-1.5">
								<Database className="h-3.5 w-3.5" />
								Cache
							</TabsTrigger>
						)}
						<TabsTrigger value="access" className="h-11 min-h-11 flex-none gap-1.5">
							<Shield className="h-3.5 w-3.5" />
							Access
						</TabsTrigger>
						<TabsTrigger value="endpoint" className="h-11 min-h-11 flex-none gap-1.5">
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
								<AccessLevelSelect
									value={accessLevel}
									onValueChange={(v) =>
										setAccessLevel(v as WorkflowAccessLevel)
									}
									className="min-h-11"
								/>
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
												aria-label="Assigned roles"
												disabled={!rolesReady || isSaving}
												className="min-h-11 w-full justify-between font-normal"
											>
												<span className="text-muted-foreground">
													Select roles...
												</span>
												<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
											</Button>
										</PopoverTrigger>
										<PopoverContent variant="picker"
											className="p-0"
											align="start"
										>
											<Command>
												<CommandInput
													placeholder="Search roles..."
													className="min-h-11"
												/>
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
														<div className="flex flex-1 flex-col">
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
										<div className="flex flex-wrap gap-2 rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/50 p-2">
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
														<Button variant="ghost" size="icon-lg" aria-label={`Remove ${role?.name || roleId} role`} disabled={isSaving || !rolesReady} onClick={() => handleRoleToggle(roleId)}><X className="h-3 w-3" /></Button>
													</Badge>
												);
											})}
										</div>
									)}

									<p className="text-xs text-muted-foreground">
										Users must have at least one of these roles to execute this
										workflow
									</p>

									{rolesReady && selectedRoleIds.length === 0 && (
										<p className="text-xs text-[var(--bf-warning)]">
											No roles assigned - only platform admins can execute this
											workflow
										</p>
									)}
								</div>
							)}

							{workflow && (
								<WorkflowAccessPanel key={workflow.id} workflow={workflow} />
							)}
						</TabsContent>

						{/* Endpoint Tab */}
						<TabsContent value="endpoint" className="mt-0 space-y-4">
							<div className="flex items-center justify-between gap-4">
								<div className="space-y-0.5">
									<Label htmlFor="workflow-endpoint-enabled">Enable HTTP Endpoint</Label>
									<p className="text-xs text-muted-foreground">
										Expose this workflow as an HTTP API endpoint
									</p>
								</div>
								<Switch
									id="workflow-endpoint-enabled" checked={endpointEnabled}
									onCheckedChange={setEndpointEnabled}
								/>
							</div>

							{endpointEnabled && (
								<>
									{/* Execution Mode */}
									<div className="space-y-2">
										<Label htmlFor="workflow-endpoint-mode">Execution Mode</Label>
								<Select
									value={executionMode}
									onValueChange={(v) => setExecutionMode(v as "sync" | "async")}
								>
									<SelectTrigger id="workflow-endpoint-mode" className="min-h-11">
										<SelectValue>{executionMode === "sync" ? "Synchronous" : "Asynchronous"}</SelectValue>
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
 aria-pressed={allowedMethods.includes(method)} className="min-h-11"
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

									<div className="flex items-center justify-between gap-4">
										<div className="space-y-0.5">
											<Label htmlFor="workflow-endpoint-public">Public Endpoint</Label>
											<p className="text-xs text-muted-foreground">
												Skip authentication (use for incoming webhooks)
											</p>
										</div>
										<Switch
											id="workflow-endpoint-public" checked={publicEndpoint}
											onCheckedChange={setPublicEndpoint}
										/>
									</div>

									<div className="flex items-center justify-between gap-4">
										<div className="space-y-0.5">
											<Label htmlFor="workflow-endpoint-global-key">Disable Global API Key</Label>
											<p className="text-xs text-muted-foreground">
												Only workflow-specific API keys will work
											</p>
										</div>
										<Switch
											id="workflow-endpoint-global-key" checked={disableGlobalKey}
											onCheckedChange={setDisableGlobalKey}
										/>
									</div>

									{/* Endpoint URL */}
									<div className="space-y-2">
										<Label htmlFor="workflow-endpoint-url">Endpoint URL</Label>
										<Input
											id="workflow-endpoint-url" value={endpointUrl}
											readOnly
											className="min-h-11 font-mono text-xs [overflow-wrap:anywhere]"
										/>
									</div>

									{/* API Key Management - Hidden for public endpoints */}
									{!isPublicEndpoint && (
										<div className="space-y-2">
											<Label>Workflow API Key</Label>
											{keyError && <div role="alert" ref={keyErrorRef} tabIndex={-1} className="space-y-2 rounded-[var(--bf-radius-surface)] border border-destructive/30 p-3 text-sm text-destructive outline-none"><p>{keyError}</p><Button variant="outline" className="min-h-11" disabled={keysFetching || keysLoading || keysError} onClick={() => void handleGenerateKey()}>Retry key generation</Button></div>}
											{keysLoading ? <p role="status" className="text-sm text-muted-foreground">Loading API keys…</p> : keysError ? <div role="alert" className="space-y-2 text-sm"><p>Could not load workflow API keys.</p><Button variant="outline" className="min-h-11" disabled={keysFetching} onClick={() => void refetchKeys()}>Retry API keys</Button></div> : hasKey ? (
												<div className="flex items-center gap-2">
													<Input
														type="text"
														aria-label="Workflow API key" value={displayKey}
														readOnly
														className="min-h-11 flex-1 font-mono text-xs [overflow-wrap:anywhere]"
													/>
													<Button
														variant="outline"
														size="sm"
														className="min-h-11"
														onClick={handleGenerateKey}
														disabled={keysFetching || createKeyMutation.isPending || revokeKeyMutation.isPending}
														title="Regenerate API key"
													>
														<RefreshCw
															className={cn(
																"h-4 w-4",
																(createKeyMutation.isPending ||
																	revokeKeyMutation.isPending) &&
																	!prefersReducedMotion &&
																	"motion-safe:animate-spin",
															)}
														/>
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
														className="min-h-11"
														onClick={handleGenerateKey}
														disabled={keysFetching || createKeyMutation.isPending}
													>
														{createKeyMutation.isPending ? (
															<>
																<RefreshCw
																	className={cn(
																		"mr-2 h-4 w-4",
																		!prefersReducedMotion &&
																			"motion-safe:animate-spin",
																	)}
																/>
																Generating...
															</>
														) : (
															"Generate Key"
														)}
													</Button>
												</div>
											)}
											<p className="text-xs text-muted-foreground">
												{keysLoading || keysError ? "Load existing keys before generating or regenerating a key." : hasKey
													? "This key is specific to this workflow. Click refresh to regenerate."
													: "Generate a workflow-specific API key for authenticating HTTP requests."}
											</p>
										</div>
									)}

									{/* cURL Example */}
									<div className="space-y-2">
										<Label>Example Request</Label>
										<div className="relative">
											<pre className="rounded-[var(--bf-radius-control)] bg-muted p-4 pr-16 text-xs overflow-x-auto ring-1 ring-foreground/5">
												<code className="[overflow-wrap:anywhere] whitespace-pre-wrap">
													{curlExample}
												</code>
											</pre>
											<Button
												variant="ghost"
												size="sm"
												className="absolute right-2 top-2 min-h-11 bg-muted"
												aria-label={
													curlCopyState === "copying"
														? "Copying cURL example"
														: curlCopyState === "copied"
															? "Copied cURL example"
															: curlCopyState === "error"
																? "Retry cURL example copy"
																: "Copy cURL example"
												}
												title={
													curlCopyState === "copying"
														? "Copying cURL example"
														: curlCopyState === "copied"
															? "Copied cURL example"
															: curlCopyState === "error"
																? "Retry cURL example copy"
																: "Copy cURL example"
												}
												disabled={curlCopyState === "copying"}
												onClick={() => void copyCurlExample()}
											>
												{curlCopyState === "copied" ? (
													<Check className="h-3 w-3" />
												) : (
													<Copy className="h-3 w-3" />
												)}
											</Button>
										</div>
										{curlCopyState === "error" ? (
											<p role="alert" ref={copyErrorRef} tabIndex={-1} className="text-xs leading-5 text-[var(--bf-danger)]">
												Could not copy the cURL example. Try again, or select the request text and copy it manually.
											</p>
										) : curlCopyState === "copied" ? (
											<p role="status" className="text-xs leading-5 text-muted-foreground">
												cURL example copied
											</p>
										) : null}
									</div>
								</>
							)}
						</TabsContent>
					</div>
				</Tabs>
				{keyBusy && <p role="status" className="shrink-0 text-sm text-muted-foreground">Updating API key…</p>}

				{!rolesReady && (
					<div role={rolesLoadError ? "alert" : "status"} className="shrink-0 text-sm text-muted-foreground">
						{rolesLoadError ? <><p>Could not load assigned roles. Load them before saving to preserve access settings.</p><Button variant="outline" className="mt-2 min-h-11" onClick={() => { setRolesLoadError(false); setRolesLoadAttempt((attempt) => attempt + 1); }}>Retry loading roles</Button></> : "Loading access settings…"}
					</div>
				)}
				{saveError && (
					<p ref={saveErrorRef} role="alert" tabIndex={-1} className="max-h-24 shrink-0 overflow-y-auto rounded-[var(--bf-radius-surface)] border border-destructive/30 p-3 text-sm text-destructive outline-none [overflow-wrap:anywhere]">
						{saveError}
					</p>
				)}
				<DialogFooter className="shrink-0 gap-2 border-t border-border/70 pt-4">
					<Button
						variant="outline"
						className="min-h-11"
						onClick={handleClose}
						disabled={isSaving || keyBusy}
					>
						Cancel
					</Button>
					<Button
						className="min-h-11"
						onClick={handleSave}
						disabled={isSaving || keyBusy || isSolutionManaged || !rolesReady}
					>
						{isSaving && (
							<Loader2
								className={cn(
									"mr-2 h-4 w-4",
									!prefersReducedMotion && "motion-safe:animate-spin",
								)}
							/>
						)}
						{isSaving ? "Saving..." : saveError ? "Retry save" : "Save Changes"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
