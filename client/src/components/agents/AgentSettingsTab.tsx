import { AgentMCPConnectionsPanel } from "./AgentMCPConnectionsPanel";
import { SettingsResourceNotice } from "./SettingsResourceNotice";
/**
 * Settings tab for an agent's detail page.
 *
 * Full-parity form for AgentCreate / AgentUpdate. Field set matches the
 * deleted AgentDialog (see git show d1eaef49^:AgentDialog.tsx) restyled
 * as the mockup's `.form-section` single-column form:
 *   - Identity       — Organization (admin), Name, Description, Access level,
 *                      Assigned roles (when role_based), Activation switch
 *   - Behavior       — System prompt, Channels
 *   - Tools & Knowledge — Tools (system + workflow grouped), Delegated agents,
 *                      Knowledge sources
 *   - Model          — llm_profile_id picker, llm_max_tokens, max_iterations,
 *                      max_token_budget (platform admins only)
 *
 * Two modes:
 *   - mode="create": empty form, POSTs /api/agents on save
 *   - mode="edit":   prepopulated from `agent`, PUTs /api/agents/:id
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { AlertTriangle, ChevronsUpDown, Info, Loader2, X } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ModelProfileSelector } from "@/components/ai/ModelProfileSelector";
import { SolutionManagedBanner } from "@/components/solutions/SolutionManagedBanner";
import { Badge } from "@/components/ui/badge";
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
	Form,
	FormControl,
	FormDescription,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { MultiCombobox } from "@/components/ui/multi-combobox";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import { Textarea } from "@/components/ui/textarea";
import {
	OrganizationSelect,
	PERSONAL_SCOPE,
} from "@/components/forms/OrganizationSelect";
import { AccessLevelSelect } from "@/components/access/AccessLevelSelect";

import {
	CARD_SURFACE,
	TYPE_LABEL_UPPERCASE,
} from "@/components/agents/design-tokens";

import { useAuth } from "@/contexts/AuthContext";
import { useQuery } from "@tanstack/react-query";
import {
	listModelProfiles,
	type AIModelProfile,
} from "@/services/aiModels";
import {
	useAgents,
	useCreateAgent,
	useUpdateAgent,
	type AgentPublic,
} from "@/hooks/useAgents";
import { useKnowledgeNamespaces } from "@/hooks/useKnowledge";
import { useRoles } from "@/hooks/useRoles";
import { useToolsGrouped } from "@/hooks/useTools";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentChannel = components["schemas"]["AgentChannel"];
type AgentAccessLevel = components["schemas"]["AgentAccessLevel"];
type RolePublic = components["schemas"]["RolePublic"];

const CHANNELS: { value: AgentChannel; label: string }[] = [
	{ value: "chat", label: "Web Chat" },
];

const formSchema = z.object({
	name: z.string().min(1, "Name is required").max(100),
	description: z.string().max(500).optional(),
	system_prompt: z.string().min(1, "System prompt is required"),
	channels: z.array(z.enum(["chat", "voice", "teams", "slack"])),
	access_level: z.enum([
		"private",
		"authenticated",
		"everyone",
		"role_based",
	]),
	organization_id: z.string().nullable(),
	tool_ids: z.array(z.string()),
	system_tools: z.array(z.string()),
	delegated_agent_ids: z.array(z.string()),
	role_ids: z.array(z.string()),
	knowledge_sources: z.array(z.string()),
	mcp_connection_ids: z.array(z.string()),
	llm_profile_id: z.string().nullable(),
	llm_max_tokens: z.number().min(1).max(200_000).nullable(),
	max_iterations: z.number().min(1).max(200).nullable(),
	max_token_budget: z.number().min(1000).max(1_000_000).nullable(),
	is_active: z.boolean(),
});

type FormValues = z.infer<typeof formSchema>;

/**
 * Inherited `llm_max_tokens` hint shown when the agent field is empty.
 * Mirrors the backend precedence Agent.llm_max_tokens >
 * Profile.default_max_tokens > provider default (Anthropic requires 16,384).
 * There is NO generic harness default: empty means inherit, and a null
 * resolution renders the "Inherit" placeholder.
 *
 * @deprecated Kept for backward compatibility only. It is no longer used as
 * the default — do not reintroduce it into the inherit chain.
 */
export const HARNESS_DEFAULT_MAX_TOKENS = 8000;
export const ANTHROPIC_REQUIRED_MAX_TOKENS = 16384;

export type InheritedMaxTokensSource = "profile" | "default" | "Anthropic";
export interface InheritedMaxTokens {
	value: number;
	source: InheritedMaxTokensSource;
}

/** Profile shape with the parallel-backend `default_max_tokens` field. */
export type ModelProfileWithMaxTokens = AIModelProfile & {
	default_max_tokens?: number | null;
};

export function resolveInheritedMaxTokens(
	profile: ModelProfileWithMaxTokens | null | undefined,
	opts?: { harnessDefault?: number | null },
): InheritedMaxTokens | null {
	const harnessDefault =
		opts && "harnessDefault" in opts ? opts.harnessDefault : null;
	const profileDefault = profile?.default_max_tokens ?? null;
	if (profileDefault != null) {
		return { value: profileDefault, source: "profile" };
	}
	const provider = profile?.connection?.provider?.toLowerCase?.() ?? "";
	if (provider === "anthropic") {
		return {
			value: ANTHROPIC_REQUIRED_MAX_TOKENS,
			source: "Anthropic",
		};
	}
	if (harnessDefault != null) {
		return { value: harnessDefault, source: "default" };
	}
	return null;
}

export function formatInheritedMaxTokensPlaceholder(
	inherited: InheritedMaxTokens | null,
): string | undefined {
	if (!inherited) return undefined;
	return `${inherited.value.toLocaleString("en-US")} (${inherited.source})`;
}

export interface AgentSettingsTabProps {
	mode: "create" | "edit";
	agent?: AgentPublic | null;
	onCreated?: (newId: string) => void;
}

/**
 * Thin divided form section — mockup's `.form-section` in Tailwind.
 * Last-child drops the bottom divider via `last:border-b-0`.
 */
function FormSection({
	title,
	children,
	testId,
}: {
	title: string;
	children: React.ReactNode;
	testId?: string;
}) {
	return (
		<section
			className="min-w-0 border-b px-5 py-5 last:border-b-0"
			data-testid={testId}
		>
			<h3 className={cn("mb-3.5", TYPE_LABEL_UPPERCASE)}>{title}</h3>
			<div className="flex flex-col gap-3.5">{children}</div>
		</section>
	);
}

export function AgentSettingsTab({
	mode,
	agent,
	onCreated,
}: AgentSettingsTabProps) {
	const { isPlatformAdmin, user } = useAuth();
	const createAgent = useCreateAgent();
	const updateAgent = useUpdateAgent();

	// Solution-managed agents are read-only on the platform (criterion 6):
	// show the banner and block Save. Only meaningful in edit mode.
	const isSolutionManaged =
		mode === "edit" && (agent?.is_solution_managed ?? false);

	const {
		data: allAgents,
		isError: agentsError,
		isLoading: agentsLoading,
		isFetching: agentsFetching,
		dataUpdatedAt: agentsUpdated,
		refetch: refetchAgents,
	} = useAgents();
	const {
		data: toolsGrouped,
		isError: toolsError,
		isLoading: toolsLoading,
		isFetching: toolsFetching,
		dataUpdatedAt: toolsUpdated,
		refetch: refetchTools,
	} = useToolsGrouped({ include_inactive: true });
	const [toolsOpen, setToolsOpen] = useState(false);
	const toolsTriggerRef = useRef<HTMLButtonElement>(null);
	const delegatesTriggerRef = useRef<HTMLButtonElement>(null);
	const [delegationsOpen, setDelegationsOpen] = useState(false);
	const [rolesOpen, setRolesOpen] = useState(false);
	const rolesTriggerRef = useRef<HTMLButtonElement>(null);

	// Default: admin → null (global), org user → their own org.
	const defaultOrgId = isPlatformAdmin
		? null
		: (user?.organizationId ?? null);

	const formDefaults = useMemo<FormValues>(() => {
		if (agent) {
			const a = agent as AgentPublic & {
				organization_id?: string | null;
				system_tools?: string[];
				mcp_connection_ids?: string[];
				llm_profile_id?: string | null;
				llm_max_tokens?: number | null;
				max_iterations?: number | null;
				max_token_budget?: number | null;
			};
			return {
				name: a.name ?? "",
				description: a.description ?? "",
				system_prompt: a.system_prompt ?? "",
				channels: ((a.channels as AgentChannel[]) ?? [
					"chat",
				]) as AgentChannel[],
				access_level: (a.access_level ?? "role_based") as
					"private" | "authenticated" | "everyone" | "role_based",
				organization_id: a.organization_id ?? null,
				tool_ids: a.tool_ids ?? [],
				system_tools: a.system_tools ?? [],
				delegated_agent_ids: a.delegated_agent_ids ?? [],
				role_ids: a.role_ids ?? [],
				knowledge_sources: a.knowledge_sources ?? [],
				mcp_connection_ids: a.mcp_connection_ids ?? [],
				llm_profile_id: a.llm_profile_id ?? null,
				llm_max_tokens: a.llm_max_tokens ?? null,
				max_iterations: a.max_iterations ?? null,
				max_token_budget: a.max_token_budget ?? null,
				is_active: a.is_active ?? true,
			};
		}
		return {
			name: "",
			description: "",
			system_prompt: "",
			channels: ["chat"],
			access_level: isPlatformAdmin ? "role_based" : "private",
			organization_id: defaultOrgId,
			tool_ids: [],
			system_tools: [],
			delegated_agent_ids: [],
			role_ids: [],
			knowledge_sources: [],
			mcp_connection_ids: [],
			llm_profile_id: null,
			llm_max_tokens: null,
			max_iterations: null,
			max_token_budget: null,
			is_active: true,
		};
	}, [agent, defaultOrgId, isPlatformAdmin]);

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: formDefaults,
	});

	const setDraftValue: typeof form.setValue = (name, value, options) =>
		form.setValue(name, value, { ...options, shouldDirty: true });

	const { reset } = form;
	const loadedAgent = useRef(agent?.id);
	// Subscribe so keepDirtyValues preserves fields the user has edited.
	const dirtyFields = form.formState.dirtyFields;
	void dirtyFields;
	useEffect(() => {
		const changed = loadedAgent.current !== agent?.id;
		reset(formDefaults, { keepDirtyValues: !changed });
		loadedAgent.current = agent?.id;
	}, [agent?.id, formDefaults, reset]);
	const saveBusy = useRef(false);
	const [saving, setSaving] = useState(false);
	const [saveFailed, setSaveFailed] = useState(false);
	const saveErrorRef = useRef<HTMLParagraphElement>(null);
	useEffect(() => {
		if (saveFailed) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView?.({ block: "nearest" });
		}
	}, [saveFailed]);

	// Use `useWatch` (rather than `form.watch(name)`) so the React Compiler
	// can memoize this component — `watch()` returns a function reference
	// that cannot be memoized safely (react-hooks/incompatible-library).
	const accessLevel = useWatch({
		control: form.control,
		name: "access_level",
	});
	const shouldLoadRoles = accessLevel === "role_based";
	const {
		data: roles,
		isError: rolesError,
		isLoading: rolesLoading,
		isFetching: rolesFetching,
		dataUpdatedAt: rolesUpdated,
		refetch: refetchRoles,
	} = useRoles({ enabled: shouldLoadRoles });
	const systemTools = useWatch({
		control: form.control,
		name: "system_tools",
	});
	const toolIds = useWatch({ control: form.control, name: "tool_ids" });
	const modelProfileId = useWatch({
		control: form.control,
		name: "llm_profile_id",
	});
	const watchedOrgId = useWatch({
		control: form.control,
		name: "organization_id",
	});

	const {
		data: knowledgeNamespaces,
		isError: knowledgeError,
		isLoading: knowledgeLoading,
		isFetching: knowledgeFetching,
		dataUpdatedAt: knowledgeUpdated,
		refetch: refetchKnowledge,
	} = useKnowledgeNamespaces(watchedOrgId);

	// Resolve the selected model profile so the empty llm_max_tokens state
	// can show the inherited value + source. Same query key as
	// ModelProfileSelector, so the result is shared/cached. Only admins see
	// the budget fields, so only they need this lookup.
	const { data: modelProfiles } = useQuery({
		queryKey: ["ai", "model-profiles"],
		queryFn: listModelProfiles,
		enabled: isPlatformAdmin,
	});
	const selectedModelProfile = useMemo(
		() =>
			(modelProfiles as ModelProfileWithMaxTokens[] | undefined)?.find(
				(profile) => profile.id === modelProfileId,
			) ?? null,
		[modelProfiles, modelProfileId],
	);
	const inheritedMaxTokens = useMemo(
		() => resolveInheritedMaxTokens(selectedModelProfile),
		[selectedModelProfile],
	);
	const inheritedMaxTokensPlaceholder =
		formatInheritedMaxTokensPlaceholder(inheritedMaxTokens) ?? "Inherit";
	const profileDefaultMaxTokens =
		selectedModelProfile?.default_max_tokens ?? null;

	// Exclude the current agent from delegation options, filter out null-id entries.
	const delegationOptions = useMemo(
		() =>
			(allAgents ?? []).filter(
				(a): a is typeof a & { id: string } =>
					a.id !== null && a.id !== agent?.id,
			),
		[allAgents, agent?.id],
	);

	// ────────────────────────────────────────────────────────────────────
	// Tool-audience validation — ported from main's AgentDialog (50b405af).
	// Guards against saving an agent with workflow tools from a different
	// org. Global agents that use org-scoped tools get an informational
	// banner; a save-blocking mismatch fires the destructive banner.
	// ────────────────────────────────────────────────────────────────────
	type ToolAudience = "ok" | "mismatch" | "info-global-agent";
	const toolAudience = useCallback(
		(tool: { organization_id?: string | null }): ToolAudience => {
			const toolOrg = tool.organization_id ?? null;
			if (toolOrg === null) return "ok"; // global tool — always fine
			if (watchedOrgId === null) return "info-global-agent"; // global agent + org tool
			if (toolOrg === watchedOrgId) return "ok";
			return "mismatch";
		},
		[watchedOrgId],
	);

	// Dep is the parent object (`toolsGrouped`) rather than the property
	// (`toolsGrouped?.workflow`) to match what React Compiler infers — keeps
	// the manual memoization preservable (react-hooks/preserve-manual-memoization).
	const mismatchedToolIds = useMemo(() => {
		if (!toolsGrouped?.workflow || !toolIds) return [] as string[];
		return toolIds.filter((id) => {
			const tool = toolsGrouped.workflow.find((t) => t.id === id);
			if (!tool) return false;
			return toolAudience(tool) === "mismatch";
		});
	}, [toolIds, toolsGrouped, toolAudience]);

	const infoToolIds = useMemo(() => {
		if (watchedOrgId !== null) return [] as string[];
		if (!toolsGrouped?.workflow || !toolIds) return [] as string[];
		return toolIds.filter((id) => {
			const tool = toolsGrouped.workflow.find((t) => t.id === id);
			return !!tool && tool.organization_id != null;
		});
	}, [toolIds, toolsGrouped, watchedOrgId]);

	const hasMismatchedTools = mismatchedToolIds.length > 0;

	async function onSubmit(values: FormValues) {
		if (hasMismatchedTools || isSolutionManaged || saveBusy.current) {
			// Save-block — banner explains which tools and how to fix.
			return;
		}

		const body = {
			name: values.name,
			description: values.description || null,
			system_prompt: values.system_prompt,
			channels: values.channels,
			access_level: values.access_level as AgentAccessLevel,
			organization_id: values.organization_id,
			is_active: values.is_active,
			tool_ids: values.tool_ids,
			system_tools: values.system_tools,
			delegated_agent_ids: values.delegated_agent_ids,
			role_ids: values.access_level === "private" ? [] : values.role_ids,
			knowledge_sources: values.knowledge_sources,
			mcp_connection_ids: values.mcp_connection_ids,
			llm_profile_id: values.llm_profile_id,
			...(isPlatformAdmin
				? {
						llm_max_tokens: values.llm_max_tokens,
						max_iterations: values.max_iterations,
						max_token_budget: values.max_token_budget,
					}
				: {}),
		};

		saveBusy.current = true;
		setSaving(true);
		setSaveFailed(false);
		try {
			if (mode === "create") {
				const result = (await createAgent.mutateAsync({
					body: body as Parameters<
						typeof createAgent.mutateAsync
					>[0]["body"],
				})) as AgentPublic;
				if (result?.id) onCreated?.(result.id);
			} else if (agent?.id) {
				await updateAgent.mutateAsync({
					params: { path: { agent_id: agent.id } },
					body: body as Parameters<
						typeof updateAgent.mutateAsync
					>[0]["body"],
				});
			}
			form.reset(values);
		} catch {
			setSaveFailed(true);
		} finally {
			saveBusy.current = false;
			setSaving(false);
		}
	}

	const pending = saving || createAgent.isPending || updateAgent.isPending;
	const totalTools = (systemTools?.length ?? 0) + (toolIds?.length ?? 0);

	return (
		<Form {...form}>
			<form
				onSubmit={(event) => {
					void form.handleSubmit(onSubmit)(event);
				}}
				className={cn("overflow-hidden", CARD_SURFACE)}
				data-testid="agent-settings-form"
			>
				{isSolutionManaged && (
					<div className="px-5 pt-4">
						<SolutionManagedBanner entityLabel="agent" />
					</div>
				)}
				{agentsError ||
				toolsError ||
				(shouldLoadRoles && rolesError) ||
				agentsLoading ||
				toolsLoading ||
				(shouldLoadRoles && rolesLoading) ||
				knowledgeError ||
				knowledgeLoading ? (
					<div
						className={
							agentsError ||
							toolsError ||
							(shouldLoadRoles && rolesError) ||
							knowledgeError
								? "space-y-3 px-5 pt-5"
								: "sr-only"
						}
					>
						<SettingsResourceNotice
							resource="available agents"
							failed={agentsError}
							loading={agentsLoading}
							cached={!!agentsUpdated}
							pending={agentsFetching}
							onRetry={() => void refetchAgents()}
						/>
						<SettingsResourceNotice
							resource="available tools"
							failed={toolsError}
							loading={toolsLoading}
							cached={!!toolsUpdated}
							pending={toolsFetching}
							onRetry={() => void refetchTools()}
						/>
						{shouldLoadRoles ? (
							<SettingsResourceNotice
								resource="available roles"
								failed={rolesError}
								loading={rolesLoading}
								cached={!!rolesUpdated}
								pending={rolesFetching}
								onRetry={() => void refetchRoles()}
							/>
						) : null}
						<SettingsResourceNotice
							resource="knowledge namespaces"
							failed={knowledgeError}
							loading={knowledgeLoading}
							cached={!!knowledgeUpdated}
							pending={knowledgeFetching}
							onRetry={() => void refetchKnowledge()}
						/>
					</div>
				) : null}
				<fieldset
					disabled={pending || isSolutionManaged}
					className="min-w-0"
				>
					{/* Identity */}
					<FormSection title="Identity">
						<FormField
							control={form.control}
							name="organization_id"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Scope</FormLabel>
									<FormControl>
										<OrganizationSelect
											value={
												accessLevel === "private"
													? PERSONAL_SCOPE
													: field.value
											}
											label="Scope"
											showPersonal
											disabled={!isPlatformAdmin}
											onChange={(scope) => {
												if (scope === PERSONAL_SCOPE) {
													form.setValue(
														"access_level",
														"private",
														{ shouldDirty: true },
													);
													form.setValue(
														"role_ids",
														[],
														{ shouldDirty: true },
													);
													if (mode === "create")
														field.onChange(
															user?.organizationId ??
																null,
														);
												} else {
													field.onChange(
														scope ?? null,
													);
													if (
														accessLevel ===
														"private"
													)
														form.setValue(
															"access_level",
															"role_based",
															{
																shouldDirty: true,
															},
														);
												}
											}}
											showGlobal
										/>
									</FormControl>
									<FormDescription>
										{accessLevel === "private"
											? "Private agents are available only to their owner."
											: "Choose the organization scope, then set who can access this agent."}
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
						<FormField
							control={form.control}
							name="name"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Name</FormLabel>
									<FormControl>
										<Input
											placeholder="Sales Assistant"
											{...field}
										/>
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>
						<FormField
							control={form.control}
							name="description"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Description</FormLabel>
									<FormControl>
										<Textarea
											placeholder="What this agent specializes in"
											rows={2}
											className="resize-none"
											{...field}
										/>
									</FormControl>
									<FormDescription>
										Used for AI routing — describe what this
										agent specializes in.
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
						{accessLevel !== "private" && (
							<FormField
								control={form.control}
								name="access_level"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Access level</FormLabel>
										<FormControl>
											<AccessLevelSelect
												value={field.value}
												onValueChange={field.onChange}
												aria-label="Access level"
											/>
										</FormControl>
										<FormMessage />
									</FormItem>
								)}
							/>
						)}
						{accessLevel === "role_based" ? (
							<FormField
								control={form.control}
								name="role_ids"
								render={({ field }) => (
									<FormItem>
										<FormLabel>
											Assigned roles
											{field.value?.length > 0
												? ` (${field.value.length})`
												: ""}
										</FormLabel>
										<Popover
											open={rolesOpen}
											onOpenChange={setRolesOpen}
										>
											<PopoverTrigger asChild>
												<FormControl>
													<Button
														ref={rolesTriggerRef}
														variant="outline"
														role="combobox"
														aria-expanded={
															rolesOpen
														}
														className="min-h-11 w-full justify-between font-normal"
													>
														<span className="text-muted-foreground">
															{field.value?.length
																? `${field.value.length} selected`
																: "Select roles…"}
														</span>
														<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
													</Button>
												</FormControl>
											</PopoverTrigger>
											<PopoverContent
												variant="picker"
												className="p-0"
												align="start"
											>
												<Command>
													<CommandInput placeholder="Search roles…" />
													<CommandList>
														<CommandEmpty>
															{rolesError
																? "Role options could not be loaded."
																: "No roles found."}
														</CommandEmpty>
														<CommandGroup>
															{(roles ?? []).map(
																(
																	role: RolePublic,
																) => (
																	<CommandItem
																		key={
																			role.id
																		}
																		value={
																			role.name ??
																			""
																		}
																		data-checked={
																			field.value?.includes(
																				role.id,
																			) ??
																			false
																		}
																		onSelect={() => {
																			const current =
																				field.value ??
																				[];
																			field.onChange(
																				current.includes(
																					role.id,
																				)
																					? current.filter(
																							(
																								id,
																							) =>
																								id !==
																								role.id,
																						)
																					: [
																							...current,
																							role.id,
																						],
																			);
																		}}
																	>
																		<div className="flex flex-col flex-1">
																			<span className="font-medium">
																				{
																					role.name
																				}
																			</span>
																			{role.description ? (
																				<span className="text-xs text-muted-foreground">
																					{
																						role.description
																					}
																				</span>
																			) : null}
																		</div>
																	</CommandItem>
																),
															)}
														</CommandGroup>
													</CommandList>
												</Command>
											</PopoverContent>
										</Popover>
										{field.value?.length ? (
											<div className="flex flex-wrap gap-1.5 rounded-md bg-muted/50 ring-1 ring-foreground/5 p-2">
												{field.value.map((roleId) => {
													const role = roles?.find(
														(r: RolePublic) =>
															r.id === roleId,
													);
													return (
														<Badge
															key={roleId}
															variant="secondary"
															className="h-auto min-h-5 max-w-full gap-1 text-left leading-normal whitespace-normal [overflow-wrap:anywhere]"
														>
															{role?.name ??
																roleId}
															<button
																type="button"
																onClick={(
																	e,
																) => {
																	e.stopPropagation();
																	e.preventDefault();
																	rolesTriggerRef.current?.focus();
																	field.onChange(
																		field.value.filter(
																			(
																				id,
																			) =>
																				id !==
																				roleId,
																		),
																	);
																}}
																className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] transition-colors hover:bg-muted-foreground/20 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
																aria-label={`Remove ${role?.name ?? roleId}`}
															>
																<X className="h-3 w-3" />
															</button>
														</Badge>
													);
												})}
											</div>
										) : null}
										<FormDescription>
											Users must have at least one of
											these roles to access this agent.
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>
						) : null}
						<FormField
							control={form.control}
							name="is_active"
							render={({ field }) => (
								<FormItem className="flex items-center justify-between gap-3 rounded-md bg-muted/50 ring-1 ring-foreground/5 px-3 py-2.5">
									<div className="flex min-w-0 flex-col text-left">
										<FormLabel className="m-0">
											{field.value
												? "Agent is active"
												: "Agent is paused"}
										</FormLabel>
										<FormDescription>
											{field.value
												? "Triggers will be accepted."
												: "Triggers will be rejected."}
										</FormDescription>
									</div>
									<FormControl>
										<Switch
											aria-label="Toggle agent active"
											checked={field.value}
											onCheckedChange={field.onChange}
										/>
									</FormControl>
								</FormItem>
							)}
						/>
					</FormSection>

					{/* Behavior */}
					<FormSection title="Behavior">
						<FormField
							control={form.control}
							name="system_prompt"
							render={({ field }) => (
								<FormItem>
									<FormLabel>System prompt</FormLabel>
									<FormControl>
										<TiptapEditor
											content={field.value}
											onChange={field.onChange}
											onBlur={field.onBlur}
											ariaLabel="System prompt"
											readOnly={
												pending || isSolutionManaged
											}
											className="min-w-0"
											editorClassName="min-h-[200px] max-h-[28rem]"
											placeholder="You are a helpful assistant…"
										/>
									</FormControl>
									<FormDescription>
										Instructions the agent follows on every
										run.
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
						<FormField
							control={form.control}
							name="channels"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Channels</FormLabel>
									<FormControl>
										<MultiCombobox
											options={CHANNELS.map((c) => ({
												value: c.value,
												label: c.label,
											}))}
											value={field.value ?? []}
											onValueChange={field.onChange}
											placeholder="Select channels…"
											emptyText="No channels available."
										/>
									</FormControl>
									<FormDescription>
										Communication channels this agent is
										available on.
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
					</FormSection>

					{/* Tools & Knowledge */}
					<FormSection title="Tools & Knowledge">
						<FormItem>
							<FormLabel>
								Tools{totalTools > 0 ? ` (${totalTools})` : ""}
							</FormLabel>

							{hasMismatchedTools ? (
								<Alert
									variant="destructive"
									data-testid="tool-mismatch-banner"
								>
									<AlertTriangle className="h-4 w-4" />
									<AlertTitle>
										Tools don&apos;t match this agent&apos;s
										organization
									</AlertTitle>
									<AlertDescription>
										<span>
											Remove these tools or change the
											agent&apos;s organization:
										</span>
										<ul className="list-disc pl-5">
											{mismatchedToolIds.map((id) => {
												const tool =
													toolsGrouped?.workflow.find(
														(t) => t.id === id,
													);
												if (!tool) return null;
												const toolWithOrg =
													tool as typeof tool & {
														organization_name?:
															string | null;
													};
												return (
													<li key={id}>
														{tool.name}
														{toolWithOrg.organization_name ? (
															<span className="text-muted-foreground">
																{" "}
																(
																{
																	toolWithOrg.organization_name
																}
																)
															</span>
														) : null}
													</li>
												);
											})}
										</ul>
									</AlertDescription>
								</Alert>
							) : null}

							{infoToolIds.length > 0 ? (
								<Alert data-testid="tool-global-info-banner">
									<Info className="h-4 w-4" />
									<AlertDescription>
										This global agent uses{" "}
										{infoToolIds.length} org-scoped tool
										{infoToolIds.length === 1 ? "" : "s"}.
									</AlertDescription>
								</Alert>
							) : null}

							<Popover
								open={toolsOpen}
								onOpenChange={setToolsOpen}
							>
								<PopoverTrigger asChild>
									<Button
										variant="outline"
										role="combobox"
										aria-expanded={toolsOpen}
										aria-label="Tools"
										ref={toolsTriggerRef}
										className="h-auto min-h-11 w-full justify-between font-normal"
									>
										{totalTools > 0
											? `${totalTools} tool${totalTools === 1 ? "" : "s"} selected`
											: "Select tools…"}
										<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
									</Button>
								</PopoverTrigger>
								{totalTools > 0 ? (
									<div className="flex min-w-0 flex-1 flex-wrap gap-1">
										{systemTools?.map((toolId) => {
											const tool =
												toolsGrouped?.system.find(
													(t) => t.id === toolId,
												);
											if (!tool) return null;
											return (
												<Badge
													key={toolId}
													variant="secondary"
													className="mr-1 h-auto min-h-5 max-w-full text-left leading-normal whitespace-normal [overflow-wrap:anywhere] font-mono text-xs"
												>
													{tool.name}
													<button
														type="button"

														tabIndex={0}
														onClick={(e) => {
															e.stopPropagation();
															e.preventDefault();
															toolsTriggerRef.current?.focus();
															setDraftValue(
																"system_tools",
																systemTools.filter(
																	(id) =>
																		id !==
																		toolId,
																),
															);
														}}

														className="ml-1 inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] transition-colors hover:bg-muted-foreground/20 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
														aria-label={`Remove ${tool.name}`}
													>
														<X className="h-3 w-3" />
													</button>
												</Badge>
											);
										})}
										{toolIds?.map((toolId) => {
											const tool =
												toolsGrouped?.workflow.find(
													(t) => t.id === toolId,
												);
											if (!tool) return null;
											const deactivated = !tool.is_active;
											return (
												<Badge
													key={toolId}
													variant={
														deactivated
															? "outline"
															: "secondary"
													}
													className={cn(
														"mr-1 h-auto min-h-5 max-w-full text-left leading-normal whitespace-normal [overflow-wrap:anywhere]",
														deactivated &&
															"border-amber-500/30 bg-amber-500/10",
													)}
												>
													{deactivated ? (
														<AlertTriangle className="mr-1 h-3 w-3 text-amber-500" />
													) : null}
													{tool.name}
													<button
														type="button"

														tabIndex={0}
														onClick={(e) => {
															e.stopPropagation();
															e.preventDefault();
															toolsTriggerRef.current?.focus();
															setDraftValue(
																"tool_ids",
																toolIds.filter(
																	(id) =>
																		id !==
																		toolId,
																),
															);
														}}

														className="ml-1 inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] transition-colors hover:bg-muted-foreground/20 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
														aria-label={`Remove ${tool.name}`}
													>
														<X className="h-3 w-3" />
													</button>
												</Badge>
											);
										})}
									</div>
								) : null}
								<PopoverContent
									className="w-[min(400px,calc(100vw-2rem))] p-0"
									align="start"
								>
									<Command>
										<CommandInput placeholder="Search tools…" />
										<CommandList>
											<CommandEmpty>
												{toolsError
													? "Tool options could not be loaded."
													: "No tools found."}
											</CommandEmpty>
											{toolsGrouped?.system?.length ? (
												<CommandGroup heading="System Tools">
													{toolsGrouped.system.map(
														(tool) => (
															<CommandItem
																key={tool.id}
																value={`system-${tool.name}`}
																data-checked={
																	systemTools?.includes(
																		tool.id,
																	) ?? false
																}
																onSelect={() => {
																	const current =
																		systemTools ??
																		[];
																	setDraftValue(
																		"system_tools",
																		current.includes(
																			tool.id,
																		)
																			? current.filter(
																					(
																						id,
																					) =>
																						id !==
																						tool.id,
																				)
																			: [
																					...current,
																					tool.id,
																				],
																	);
																}}
															>
																<div className="flex min-w-0 flex-col text-left">
																	<span className="font-mono text-sm">
																		{
																			tool.id
																		}
																	</span>
																	<span className="text-xs text-muted-foreground">
																		{
																			tool.description
																		}
																	</span>
																</div>
															</CommandItem>
														),
													)}
												</CommandGroup>
											) : null}
											{toolsGrouped?.workflow?.length ? (
												<CommandGroup heading="Workflow Tools">
													{toolsGrouped.workflow.map(
														(tool) => {
															const audience =
																toolAudience(
																	tool,
																);
															const isMismatch =
																audience ===
																"mismatch";
															const isInfo =
																audience ===
																"info-global-agent";
															return (
																<CommandItem
																	key={
																		tool.id
																	}
																	value={`workflow-${tool.name}`}
																	disabled={
																		isMismatch
																	}
																	data-mismatch={
																		isMismatch
																			? "true"
																			: undefined
																	}
																	data-checked={
																		toolIds?.includes(
																			tool.id,
																		) ??
																		false
																	}
																	onSelect={() => {
																		if (
																			isMismatch
																		)
																			return;
																		const current =
																			toolIds ??
																			[];
																		setDraftValue(
																			"tool_ids",
																			current.includes(
																				tool.id,
																			)
																				? current.filter(
																						(
																							id,
																						) =>
																							id !==
																							tool.id,
																					)
																				: [
																						...current,
																						tool.id,
																					],
																		);
																	}}
																>
																	<div className="flex min-w-0 flex-col text-left">
																		<span>
																			{
																				tool.name
																			}
																			{isMismatch ? (
																				<span className="ml-2 text-[11px] text-rose-500">
																					Different
																					org
																				</span>
																			) : isInfo ? (
																				<span className="ml-2 text-[11px] text-muted-foreground">
																					Org-scoped
																				</span>
																			) : null}
																		</span>
																		{tool.description ? (
																			<span className="text-xs text-muted-foreground">
																				{
																					tool.description
																				}
																			</span>
																		) : null}
																	</div>
																</CommandItem>
															);
														},
													)}
												</CommandGroup>
											) : null}
										</CommandList>
									</Command>
								</PopoverContent>
							</Popover>
							<FormDescription>
								System tools and workflows this agent can call.
							</FormDescription>
						</FormItem>

						<FormField
							control={form.control}
							name="delegated_agent_ids"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Delegated agents</FormLabel>
									<Popover
										open={delegationsOpen}
										onOpenChange={setDelegationsOpen}
									>
										<PopoverTrigger asChild>
											<FormControl>
												<Button
													variant="outline"
													role="combobox"
													ref={delegatesTriggerRef}
													aria-expanded={
														delegationsOpen
													}
													className="h-auto min-h-11 w-full justify-between font-normal"
												>
													{field.value?.length
														? `${field.value.length} agent${field.value.length === 1 ? "" : "s"} selected`
														: "Select agents…"}
													<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
												</Button>
											</FormControl>
										</PopoverTrigger>
										{field.value?.length ? (
											<div className="flex min-w-0 flex-1 flex-wrap gap-1">
												{field.value.map((id) => {
													const delegate =
														delegationOptions.find(
															(a) => a.id === id,
														);
													return (
														<Badge
															key={id}
															variant="secondary"
															className="mr-1 h-auto min-h-5 max-w-full text-left leading-normal whitespace-normal [overflow-wrap:anywhere]"
														>
															{delegate?.name ??
																id}
															<button
																type="button"

																tabIndex={0}
																onClick={(
																	e,
																) => {
																	e.stopPropagation();
																	e.preventDefault();
																	delegatesTriggerRef.current?.focus();
																	field.onChange(
																		field.value.filter(
																			(
																				x,
																			) =>
																				x !==
																				id,
																		),
																	);
																}}

																className="ml-1 inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] transition-colors hover:bg-muted-foreground/20 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
																aria-label={`Remove ${delegate?.name ?? id}`}
															>
																<X className="h-3 w-3" />
															</button>
														</Badge>
													);
												})}
											</div>
										) : null}
										<PopoverContent
											className="w-[min(400px,calc(100vw-2rem))] p-0"
											align="start"
										>
											<Command>
												<CommandInput placeholder="Search agents…" />
												<CommandList>
													<CommandEmpty>
														{agentsError
															? "Agent options could not be loaded."
															: "No agents found."}
													</CommandEmpty>
													<CommandGroup>
														{delegationOptions.map(
															(delegate) => (
																<CommandItem
																	key={
																		delegate.id
																	}
																	value={
																		delegate.name
																	}
																	data-checked={
																		field.value?.includes(
																			delegate.id,
																		) ??
																		false
																	}
																	onSelect={() => {
																		const current =
																			field.value ??
																			[];
																		field.onChange(
																			current.includes(
																				delegate.id,
																			)
																				? current.filter(
																						(
																							id,
																						) =>
																							id !==
																							delegate.id,
																					)
																				: [
																						...current,
																						delegate.id,
																					],
																		);
																	}}
																>
																	<div className="flex min-w-0 flex-col text-left">
																		<span>
																			{
																				delegate.name
																			}
																		</span>
																		{delegate.description ? (
																			<span className="text-xs text-muted-foreground">
																				{
																					delegate.description
																				}
																			</span>
																		) : null}
																	</div>
																</CommandItem>
															),
														)}
													</CommandGroup>
												</CommandList>
											</Command>
										</PopoverContent>
									</Popover>
									<FormDescription>
										Other agents this agent can delegate
										tasks to.
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>

						<FormField
							control={form.control}
							name="knowledge_sources"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Knowledge sources</FormLabel>
									<FormControl>
										<MultiCombobox
											options={[
												...(
													knowledgeNamespaces ?? []
												).map((ns) => ({
													value: ns.namespace,
													label: ns.namespace,
													description: `${ns.scopes.total} documents`,
												})),
												...(field.value ?? [])
													.filter(
														(value) =>
															!(
																knowledgeNamespaces ??
																[]
															).some(
																(ns) =>
																	ns.namespace ===
																	value,
															),
													)
													.map((value) => ({
														value,
														label: value,
														description:
															"Saved namespace",
													})),
											]}
											value={field.value ?? []}
											onValueChange={field.onChange}
											placeholder="Select namespaces…"
											searchPlaceholder="Search namespaces…"
											isLoading={knowledgeLoading}
											emptyText={
												knowledgeError
													? "Namespaces could not be loaded."
													: "No namespaces found."
											}
										/>
									</FormControl>
									{field.value?.length ? (
										<div className="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 ring-1 ring-foreground/5 p-2">
											<Badge
												variant="secondary"
												className="font-mono text-xs"
											>
												search_knowledge
											</Badge>
											<span className="text-xs text-muted-foreground">
												tool auto-enabled
											</span>
										</div>
									) : null}
									<FormDescription>
										Namespaces this agent can search for
										context.
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
					</FormSection>

					{/* Model + Budgets */}
				</fieldset>
				{watchedOrgId ? (
					<div className="border-b px-5 py-5">
						<FormField
							control={form.control}
							name="mcp_connection_ids"
							render={({ field }) => (
								<AgentMCPConnectionsPanel
									organizationId={watchedOrgId}
									disabled={pending || isSolutionManaged}
									value={field.value ?? []}
									onChange={field.onChange}
								/>
							)}
						/>
					</div>
				) : null}
				<fieldset
					disabled={pending || isSolutionManaged}
					className="min-w-0"
				>
					<FormSection title="Model" testId="model-section">
						{isPlatformAdmin ? (
							<FormField
								control={form.control}
								name="llm_profile_id"
								render={({ field }) => (
									<FormItem>
										<ModelProfileSelector
											id="agent-model-profile"
											label="Model profile"
											value={field.value}
											onValueChange={field.onChange}
											placeholder="Use primary profile assignment"
											disabled={isSolutionManaged}
										/>
										<FormDescription>
											Choose a reusable model profile for
											this agent. Leave it unset to use
											the primary profile assignment.
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>
						) : (
							<div className="space-y-2 text-sm">
								<p className="font-medium">
									{modelProfileId
										? "Assigned model profile"
										: "Primary profile assignment"}
								</p>
								<p className="text-muted-foreground">
									A platform administrator manages model
									profiles. This assignment is preserved when
									you save other changes.
								</p>
							</div>
						)}

						{isPlatformAdmin ? (
							<div
								className="grid grid-cols-1 gap-3.5 md:grid-cols-3"
								data-testid="budget-card"
							>
								<FormField
									control={form.control}
									name="max_iterations"
									render={({ field }) => (
										<FormItem>
											<FormLabel>
												Max iterations
											</FormLabel>
											<FormControl>
												<Input
													type="number"
													placeholder="No limit"
													value={field.value ?? ""}
													onChange={(e) =>
														field.onChange(
															e.target.value
																? Number(
																		e.target
																			.value,
																	)
																: null,
														)
													}
												/>
											</FormControl>
											<FormDescription>
												Optional LLM request limit
												(1–200).
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>
								<FormField
									control={form.control}
									name="max_token_budget"
									render={({ field }) => (
										<FormItem>
											<FormLabel>
												Max token budget
											</FormLabel>
											<FormControl>
												<Input
													type="number"
													placeholder="No limit"
													value={field.value ?? ""}
													onChange={(e) =>
														field.onChange(
															e.target.value
																? Number(
																		e.target
																			.value,
																	)
																: null,
														)
													}
												/>
											</FormControl>
											<FormDescription>
												Optional cumulative limit (1k–1M
												tokens).
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>
								<FormField
									control={form.control}
									name="llm_max_tokens"
									render={({ field }) => (
										<FormItem>
											<FormLabel>
												Max tokens / response
											</FormLabel>
											<FormControl>
												<Input
													type="number"
													placeholder={
														inheritedMaxTokensPlaceholder
													}
													value={field.value ?? ""}
													onChange={(e) =>
														field.onChange(
															e.target.value
																? Number(
																		e.target
																			.value,
																	)
																: null,
														)
													}
												/>
											</FormControl>
											<FormDescription>
												Blank = inherit. Agent value
												wins when set.
											</FormDescription>
											{profileDefaultMaxTokens !=
											null ? (
												<p
													className="text-xs text-muted-foreground"
													data-testid="profile-default-max-tokens"
												>
													Profile default:{" "}
													{profileDefaultMaxTokens.toLocaleString(
														"en-US",
													)}
												</p>
											) : null}
											<FormMessage />
										</FormItem>
									)}
								/>
							</div>
						) : null}
					</FormSection>

					{saveFailed ? (
						<p
							role="alert"
							ref={saveErrorRef}
							tabIndex={-1}
							className="mx-5 mb-4 rounded-[var(--bf-radius-control)] border bg-[var(--bf-warning-soft)] p-3 text-sm"
						>
							Could not save the agent. Your changes are still
							here. Try saving again.
						</p>
					) : null}
					<div className="flex items-center justify-end gap-2 border-t bg-muted/30 px-5 py-3">
						<Button
							type="submit"
							disabled={
								pending ||
								hasMismatchedTools ||
								isSolutionManaged
							}
							data-testid="save-agent-button"
						>
							{pending ? (
								<>
									<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
									Saving…
								</>
							) : saveFailed ? (
								"Retry save"
							) : mode === "create" ? (
								"Create agent"
							) : (
								"Save changes"
							)}
						</Button>
					</div>
				</fieldset>
			</form>
		</Form>
	);
}
