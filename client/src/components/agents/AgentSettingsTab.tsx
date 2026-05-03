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
 *   - Model          — llm_model picker, llm_max_tokens, max_iterations,
 *                      max_token_budget (platform admins only)
 *
 * Two modes:
 *   - mode="create": empty form, POSTs /api/agents on save
 *   - mode="edit":   prepopulated from `agent`, PUTs /api/agents/:id
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import {
	AlertTriangle,
	Check,
	ChevronsUpDown,
	Info,
	Loader2,
	X,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox } from "@/components/ui/combobox";
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
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";

import {
	CARD_SURFACE,
	TYPE_LABEL_UPPERCASE,
} from "@/components/agents/design-tokens";

import { useAuth } from "@/contexts/AuthContext";
import {
	useAgents,
	useCreateAgent,
	useUpdateAgent,
	type AgentPublic,
} from "@/hooks/useAgents";
import { useKnowledgeNamespaces } from "@/hooks/useKnowledge";
import { useLLMModels } from "@/hooks/useLLMConfig";
import { useRoles } from "@/hooks/useRoles";
import { useToolsGrouped } from "@/hooks/useTools";
import { $api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentChannel = components["schemas"]["AgentChannel"];
type AgentAccessLevel = components["schemas"]["AgentAccessLevel"];
type RolePublic = components["schemas"]["RolePublic"];

const CHANNELS: { value: AgentChannel; label: string }[] = [
	{ value: "chat", label: "Web Chat" },
];

const ACCESS_LEVELS: {
	value: AgentAccessLevel;
	label: string;
	description: string;
}[] = [
	{
		value: "authenticated",
		label: "Authenticated",
		description: "Available to all authenticated users",
	},
	{
		value: "role_based",
		label: "Role-based",
		description: "Only assigned roles (none = platform admin only)",
	},
];

const formSchema = z.object({
	name: z.string().min(1, "Name is required").max(100),
	description: z.string().max(500).optional(),
	system_prompt: z.string().min(1, "System prompt is required"),
	channels: z.array(z.enum(["chat", "voice", "teams", "slack"])),
	access_level: z.enum(["authenticated", "role_based"]),
	organization_id: z.string().nullable(),
	tool_ids: z.array(z.string()),
	system_tools: z.array(z.string()),
	delegated_agent_ids: z.array(z.string()),
	role_ids: z.array(z.string()),
	knowledge_sources: z.array(z.string()),
	mcp_connection_ids: z.array(z.string()),
	llm_model: z.string().nullable(),
	llm_max_tokens: z.number().min(1).max(200_000).nullable(),
	max_iterations: z.number().min(1).max(200).nullable(),
	max_token_budget: z.number().min(1000).max(1_000_000).nullable(),
	is_active: z.boolean(),
});

type FormValues = z.infer<typeof formSchema>;

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
			className="border-b px-5 py-5 last:border-b-0"
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

	const { data: allAgents } = useAgents();
	const { data: toolsGrouped } = useToolsGrouped({ include_inactive: true });
	const { data: roles } = useRoles();
	const { models: availableModels } = useLLMModels();

	const [toolsOpen, setToolsOpen] = useState(false);
	const [delegationsOpen, setDelegationsOpen] = useState(false);
	const [rolesOpen, setRolesOpen] = useState(false);

	// Default: admin → null (global), org user → their own org.
	const defaultOrgId = isPlatformAdmin ? null : (user?.organizationId ?? null);

	const formDefaults = useMemo<FormValues>(() => {
		if (agent) {
			const a = agent as AgentPublic & {
				organization_id?: string | null;
				system_tools?: string[];
				mcp_connection_ids?: string[];
				llm_model?: string | null;
				llm_max_tokens?: number | null;
				max_iterations?: number | null;
				max_token_budget?: number | null;
			};
			return {
				name: a.name ?? "",
				description: a.description ?? "",
				system_prompt: a.system_prompt ?? "",
				channels: ((a.channels as AgentChannel[]) ?? ["chat"]) as AgentChannel[],
				access_level: (a.access_level ?? "role_based") as
					| "authenticated"
					| "role_based",
				organization_id: a.organization_id ?? null,
				tool_ids: a.tool_ids ?? [],
				system_tools: a.system_tools ?? [],
				delegated_agent_ids: a.delegated_agent_ids ?? [],
				role_ids: a.role_ids ?? [],
				knowledge_sources: a.knowledge_sources ?? [],
				mcp_connection_ids: a.mcp_connection_ids ?? [],
				llm_model: a.llm_model ?? null,
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
			access_level: "role_based",
			organization_id: defaultOrgId,
			tool_ids: [],
			system_tools: [],
			delegated_agent_ids: [],
			role_ids: [],
			knowledge_sources: [],
			mcp_connection_ids: [],
			llm_model: null,
			llm_max_tokens: null,
			max_iterations: null,
			max_token_budget: null,
			is_active: true,
		};
	}, [agent, defaultOrgId]);

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: formDefaults,
		values: agent ? formDefaults : undefined,
	});

	useEffect(() => {
		if (agent) form.reset(formDefaults);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [agent?.id]);

	// Use `useWatch` (rather than `form.watch(name)`) so the React Compiler
	// can memoize this component — `watch()` returns a function reference
	// that cannot be memoized safely (react-hooks/incompatible-library).
	const accessLevel = useWatch({ control: form.control, name: "access_level" });
	const systemTools = useWatch({ control: form.control, name: "system_tools" });
	const toolIds = useWatch({ control: form.control, name: "tool_ids" });
	const watchedOrgId = useWatch({ control: form.control, name: "organization_id" });

	const { data: knowledgeNamespaces } = useKnowledgeNamespaces(watchedOrgId);

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
		if (hasMismatchedTools) {
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
			role_ids: values.role_ids,
			knowledge_sources: values.knowledge_sources,
			mcp_connection_ids: values.mcp_connection_ids,
			llm_model: values.llm_model,
			...(isPlatformAdmin
				? {
						llm_max_tokens: values.llm_max_tokens,
						max_iterations: values.max_iterations,
						max_token_budget: values.max_token_budget,
					}
				: {}),
		};

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
	}

	const pending = createAgent.isPending || updateAgent.isPending;
	const totalTools = (systemTools?.length ?? 0) + (toolIds?.length ?? 0);

	return (
		<Form {...form}>
			<form
				onSubmit={form.handleSubmit(onSubmit)}
				className={cn("overflow-hidden", CARD_SURFACE)}
				data-testid="agent-settings-form"
			>
				{/* Identity */}
				<FormSection title="Identity">
					{isPlatformAdmin ? (
						<FormField
							control={form.control}
							name="organization_id"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Organization</FormLabel>
									<FormControl>
										<OrganizationSelect
											value={field.value}
											onChange={field.onChange}
											showGlobal
										/>
									</FormControl>
									<FormDescription>
										Global agents are available to every organization.
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
					) : null}
					<FormField
						control={form.control}
						name="name"
						render={({ field }) => (
							<FormItem>
								<FormLabel>Name</FormLabel>
								<FormControl>
									<Input placeholder="Sales Assistant" {...field} />
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
									Used for AI routing — describe what this agent specializes
									in.
								</FormDescription>
								<FormMessage />
							</FormItem>
						)}
					/>
					<FormField
						control={form.control}
						name="access_level"
						render={({ field }) => (
							<FormItem>
								<FormLabel>Access level</FormLabel>
								<Select
									value={field.value}
									onValueChange={field.onChange}
								>
									<FormControl>
										<SelectTrigger aria-label="Access level">
											<SelectValue />
										</SelectTrigger>
									</FormControl>
									<SelectContent>
										{ACCESS_LEVELS.map((lvl) => (
											<SelectItem key={lvl.value} value={lvl.value}>
												<div className="flex flex-col">
													<span>{lvl.label}</span>
													<span className="text-xs text-muted-foreground">
														{lvl.description}
													</span>
												</div>
											</SelectItem>
										))}
									</SelectContent>
								</Select>
								<FormMessage />
							</FormItem>
						)}
					/>
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
									<Popover open={rolesOpen} onOpenChange={setRolesOpen}>
										<PopoverTrigger asChild>
											<FormControl>
												<Button
													variant="outline"
													role="combobox"
													aria-expanded={rolesOpen}
													className="w-full justify-between font-normal"
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
											className="w-[var(--radix-popover-trigger-width)] p-0"
											align="start"
										>
											<Command>
												<CommandInput placeholder="Search roles…" />
												<CommandList>
													<CommandEmpty>No roles found.</CommandEmpty>
													<CommandGroup>
														{(roles ?? []).map((role: RolePublic) => (
															<CommandItem
																key={role.id}
																value={role.name ?? ""}
																onSelect={() => {
																	const current = field.value ?? [];
																	field.onChange(
																		current.includes(role.id)
																			? current.filter(
																					(id) => id !== role.id,
																				)
																			: [...current, role.id],
																	);
																}}
															>
																<div className="flex flex-1 items-center gap-2">
																	<Checkbox
																		checked={field.value?.includes(
																			role.id,
																		)}
																	/>
																	<div className="flex flex-col">
																		<span className="font-medium">
																			{role.name}
																		</span>
																		{role.description ? (
																			<span className="text-xs text-muted-foreground">
																				{role.description}
																			</span>
																		) : null}
																	</div>
																</div>
																<Check
																	className={cn(
																		"ml-auto h-4 w-4",
																		field.value?.includes(role.id)
																			? "opacity-100"
																			: "opacity-0",
																	)}
																/>
															</CommandItem>
														))}
													</CommandGroup>
												</CommandList>
											</Command>
										</PopoverContent>
									</Popover>
									{field.value?.length ? (
										<div className="flex flex-wrap gap-1.5 rounded-md border bg-muted/40 p-2">
											{field.value.map((roleId) => {
												const role = roles?.find(
													(r: RolePublic) => r.id === roleId,
												);
												return (
													<Badge
														key={roleId}
														variant="secondary"
														className="gap-1"
													>
														{role?.name ?? roleId}
														<button
															type="button"
															onClick={(e) => {
																e.stopPropagation();
																e.preventDefault();
																field.onChange(
																	field.value.filter(
																		(id) => id !== roleId,
																	),
																);
															}}
															className="rounded-full p-0.5 transition-colors hover:bg-muted-foreground/20"
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
										Users must have at least one of these roles to access this
										agent.
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
							<FormItem className="flex items-center justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2.5">
								<div className="flex flex-col">
									<FormLabel className="m-0">
										{field.value ? "Agent is active" : "Agent is paused"}
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
									<Textarea
										className="min-h-[200px] font-mono text-sm"
										placeholder="You are a helpful assistant…"
										{...field}
									/>
								</FormControl>
								<FormDescription>
									Instructions the agent follows on every run.
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
									Communication channels this agent is available on.
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
									Tools don&apos;t match this agent&apos;s organization
								</AlertTitle>
								<AlertDescription>
									<span>
										Remove these tools or change the agent&apos;s
										organization:
									</span>
									<ul className="list-disc pl-5">
										{mismatchedToolIds.map((id) => {
											const tool = toolsGrouped?.workflow.find(
												(t) => t.id === id,
											);
											if (!tool) return null;
											const toolWithOrg = tool as typeof tool & {
												organization_name?: string | null;
											};
											return (
												<li key={id}>
													{tool.name}
													{toolWithOrg.organization_name ? (
														<span className="text-muted-foreground">
															{" "}
															({toolWithOrg.organization_name})
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
									This global agent uses {infoToolIds.length} org-scoped
									tool
									{infoToolIds.length === 1 ? "" : "s"}.
								</AlertDescription>
							</Alert>
						) : null}

						<Popover open={toolsOpen} onOpenChange={setToolsOpen}>
							<PopoverTrigger asChild>
								<Button
									variant="outline"
									role="combobox"
									aria-expanded={toolsOpen}
									className="h-auto min-h-10 w-full justify-between font-normal"
								>
									{totalTools > 0 ? (
										<div className="flex flex-wrap gap-1">
											{systemTools?.map((toolId) => {
												const tool = toolsGrouped?.system.find(
													(t) => t.id === toolId,
												);
												if (!tool) return null;
												return (
													<Badge
														key={toolId}
														variant="secondary"
														className="mr-1 font-mono text-xs"
													>
														{tool.name}
														<span
															role="button"
															tabIndex={0}
															onClick={(e) => {
																e.stopPropagation();
																e.preventDefault();
																form.setValue(
																	"system_tools",
																	systemTools.filter(
																		(id) => id !== toolId,
																	),
																);
															}}
															onKeyDown={(e) => {
																if (e.key === "Enter" || e.key === " ") {
																	e.stopPropagation();
																	e.preventDefault();
																	form.setValue(
																		"system_tools",
																		systemTools.filter(
																			(id) => id !== toolId,
																		),
																	);
																}
															}}
															className="ml-1 cursor-pointer rounded-full p-0.5 transition-colors hover:bg-muted-foreground/20"
															aria-label={`Remove ${tool.name}`}
														>
															<X className="h-3 w-3" />
														</span>
													</Badge>
												);
											})}
											{toolIds?.map((toolId) => {
												const tool = toolsGrouped?.workflow.find(
													(t) => t.id === toolId,
												);
												if (!tool) return null;
												const deactivated = !tool.is_active;
												return (
													<Badge
														key={toolId}
														variant={deactivated ? "outline" : "secondary"}
														className={cn(
															"mr-1",
															deactivated &&
																"border-amber-500/30 bg-amber-500/10",
														)}
													>
														{deactivated ? (
															<AlertTriangle className="mr-1 h-3 w-3 text-amber-500" />
														) : null}
														{tool.name}
														<span
															role="button"
															tabIndex={0}
															onClick={(e) => {
																e.stopPropagation();
																e.preventDefault();
																form.setValue(
																	"tool_ids",
																	toolIds.filter((id) => id !== toolId),
																);
															}}
															onKeyDown={(e) => {
																if (e.key === "Enter" || e.key === " ") {
																	e.stopPropagation();
																	e.preventDefault();
																	form.setValue(
																		"tool_ids",
																		toolIds.filter(
																			(id) => id !== toolId,
																		),
																	);
																}
															}}
															className="ml-1 cursor-pointer rounded-full p-0.5 transition-colors hover:bg-muted-foreground/20"
															aria-label={`Remove ${tool.name}`}
														>
															<X className="h-3 w-3" />
														</span>
													</Badge>
												);
											})}
										</div>
									) : (
										<span className="text-muted-foreground">
											Select tools…
										</span>
									)}
									<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
								</Button>
							</PopoverTrigger>
							<PopoverContent className="w-[400px] p-0" align="start">
								<Command>
									<CommandInput placeholder="Search tools…" />
									<CommandList>
										<CommandEmpty>No tools found.</CommandEmpty>
										{toolsGrouped?.system?.length ? (
											<CommandGroup heading="System Tools">
												{toolsGrouped.system.map((tool) => (
													<CommandItem
														key={tool.id}
														value={`system-${tool.name}`}
														onSelect={() => {
															const current = systemTools ?? [];
															form.setValue(
																"system_tools",
																current.includes(tool.id)
																	? current.filter((id) => id !== tool.id)
																	: [...current, tool.id],
															);
														}}
													>
														<Check
															className={cn(
																"mr-2 h-4 w-4",
																systemTools?.includes(tool.id)
																	? "opacity-100"
																	: "opacity-0",
															)}
														/>
														<div className="flex flex-col">
															<span className="font-mono text-sm">
																{tool.id}
															</span>
															<span className="text-xs text-muted-foreground">
																{tool.description}
															</span>
														</div>
													</CommandItem>
												))}
											</CommandGroup>
										) : null}
										{toolsGrouped?.workflow?.length ? (
											<CommandGroup heading="Workflow Tools">
												{toolsGrouped.workflow.map((tool) => {
													const audience = toolAudience(tool);
													const isMismatch = audience === "mismatch";
													const isInfo =
														audience === "info-global-agent";
													return (
														<CommandItem
															key={tool.id}
															value={`workflow-${tool.name}`}
															disabled={isMismatch}
															data-mismatch={
																isMismatch ? "true" : undefined
															}
															onSelect={() => {
																if (isMismatch) return;
																const current = toolIds ?? [];
																form.setValue(
																	"tool_ids",
																	current.includes(tool.id)
																		? current.filter(
																				(id) => id !== tool.id,
																			)
																		: [...current, tool.id],
																);
															}}
														>
															<Check
																className={cn(
																	"mr-2 h-4 w-4",
																	toolIds?.includes(tool.id)
																		? "opacity-100"
																		: "opacity-0",
																)}
															/>
															<div className="flex flex-col">
																<span>
																	{tool.name}
																	{isMismatch ? (
																		<span className="ml-2 text-[11px] text-rose-500">
																			Different org
																		</span>
																	) : isInfo ? (
																		<span className="ml-2 text-[11px] text-muted-foreground">
																			Org-scoped
																		</span>
																	) : null}
																</span>
																{tool.description ? (
																	<span className="text-xs text-muted-foreground">
																		{tool.description}
																	</span>
																) : null}
															</div>
														</CommandItem>
													);
												})}
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

					{/*
					 * External MCP tools — per-connection grants. An admin
					 * checks each connection the agent should be allowed to
					 * call, and the tools published by that connection are
					 * shown as muted hints below the checkbox so it's clear
					 * what's being granted. New agents start with zero
					 * grants (deny-by-default); the migration that
					 * introduced this surface backfilled grants for agents
					 * that existed prior to the rollout. Hidden entirely
					 * for platform-level (organization_id=null) agents
					 * since MCP connections are strictly per-org.
					 */}
					<FormField
						control={form.control}
						name="mcp_connection_ids"
						render={({ field }) => (
							<AgentMCPConnectionsPanel
								organizationId={watchedOrgId}
								value={field.value ?? []}
								onChange={field.onChange}
							/>
						)}
					/>

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
												aria-expanded={delegationsOpen}
												className="h-auto min-h-10 w-full justify-between font-normal"
											>
												{field.value?.length ? (
													<div className="flex flex-wrap gap-1">
														{field.value.map((id) => {
															const delegate = delegationOptions.find(
																(a) => a.id === id,
															);
															return (
																<Badge
																	key={id}
																	variant="secondary"
																	className="mr-1"
																>
																	{delegate?.name ?? id}
																	<span
																		role="button"
																		tabIndex={0}
																		onClick={(e) => {
																			e.stopPropagation();
																			e.preventDefault();
																			field.onChange(
																				field.value.filter(
																					(x) => x !== id,
																				),
																			);
																		}}
																		onKeyDown={(e) => {
																			if (
																				e.key === "Enter" ||
																				e.key === " "
																			) {
																				e.stopPropagation();
																				e.preventDefault();
																				field.onChange(
																					field.value.filter(
																						(x) => x !== id,
																					),
																				);
																			}
																		}}
																		className="ml-1 cursor-pointer rounded-full p-0.5 transition-colors hover:bg-muted-foreground/20"
																		aria-label={`Remove ${delegate?.name ?? id}`}
																	>
																		<X className="h-3 w-3" />
																	</span>
																</Badge>
															);
														})}
													</div>
												) : (
													<span className="text-muted-foreground">
														Select agents…
													</span>
												)}
												<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
											</Button>
										</FormControl>
									</PopoverTrigger>
									<PopoverContent className="w-[400px] p-0" align="start">
										<Command>
											<CommandInput placeholder="Search agents…" />
											<CommandList>
												<CommandEmpty>No agents found.</CommandEmpty>
												<CommandGroup>
													{delegationOptions.map((delegate) => (
														<CommandItem
															key={delegate.id}
															value={delegate.name}
															onSelect={() => {
																const current = field.value ?? [];
																field.onChange(
																	current.includes(delegate.id)
																		? current.filter(
																				(id) => id !== delegate.id,
																			)
																		: [...current, delegate.id],
																);
															}}
														>
															<Check
																className={cn(
																	"mr-2 h-4 w-4",
																	field.value?.includes(delegate.id)
																		? "opacity-100"
																		: "opacity-0",
																)}
															/>
															<div className="flex flex-col">
																<span>{delegate.name}</span>
																{delegate.description ? (
																	<span className="text-xs text-muted-foreground">
																		{delegate.description}
																	</span>
																) : null}
															</div>
														</CommandItem>
													))}
												</CommandGroup>
											</CommandList>
										</Command>
									</PopoverContent>
								</Popover>
								<FormDescription>
									Other agents this agent can delegate tasks to.
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
										options={(knowledgeNamespaces ?? []).map((ns) => ({
											value: ns.namespace,
											label: ns.namespace,
											description: `${ns.scopes.total} documents`,
										}))}
										value={field.value ?? []}
										onValueChange={field.onChange}
										placeholder="Select namespaces…"
										searchPlaceholder="Search namespaces…"
										emptyText="No namespaces found."
									/>
								</FormControl>
								{field.value?.length ? (
									<div className="flex items-center gap-2 rounded-md border bg-muted/30 p-2">
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
									Namespaces this agent can search for context.
								</FormDescription>
								<FormMessage />
							</FormItem>
						)}
					/>
				</FormSection>

				{/* Model + Budgets */}
				<FormSection title="Model" testId="model-section">
					<FormField
						control={form.control}
						name="llm_model"
						render={({ field }) => (
							<FormItem>
								<FormLabel>Model</FormLabel>
								<FormControl>
									{availableModels.length > 0 ? (
										<Combobox
											value={field.value ?? "__default__"}
											onValueChange={(v) =>
												field.onChange(v === "__default__" ? null : v)
											}
											placeholder="Use platform default"
											searchPlaceholder="Search models…"
											emptyText="No models found."
											options={[
												{
													value: "__default__",
													label: "Use platform default",
												},
												...availableModels.map((m) => ({
													value: m.id,
													label: m.display_name,
												})),
											]}
										/>
									) : (
										<Input
											placeholder="Enter model identifier (leave empty for default)"
											value={field.value ?? ""}
											onChange={(e) =>
												field.onChange(e.target.value || null)
											}
										/>
									)}
								</FormControl>
								<FormDescription>
									Override the platform default for this agent only.
								</FormDescription>
								<FormMessage />
							</FormItem>
						)}
					/>

					{isPlatformAdmin ? (
						<div className="grid grid-cols-1 gap-3.5 md:grid-cols-3" data-testid="budget-card">
							<FormField
								control={form.control}
								name="max_iterations"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Max iterations</FormLabel>
										<FormControl>
											<Input
												type="number"
												placeholder="50"
												value={field.value ?? ""}
												onChange={(e) =>
													field.onChange(
														e.target.value
															? Number(e.target.value)
															: null,
													)
												}
											/>
										</FormControl>
										<FormDescription>LLM round-trips (1–200).</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>
							<FormField
								control={form.control}
								name="max_token_budget"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Max token budget</FormLabel>
										<FormControl>
											<Input
												type="number"
												placeholder="100000"
												value={field.value ?? ""}
												onChange={(e) =>
													field.onChange(
														e.target.value
															? Number(e.target.value)
															: null,
													)
												}
											/>
										</FormControl>
										<FormDescription>Total tokens (1k–1M).</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>
							<FormField
								control={form.control}
								name="llm_max_tokens"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Max tokens / response</FormLabel>
										<FormControl>
											<Input
												type="number"
												value={field.value ?? ""}
												onChange={(e) =>
													field.onChange(
														e.target.value
															? Number(e.target.value)
															: null,
													)
												}
											/>
										</FormControl>
										<FormDescription>
											Per LLM call (model maximum).
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>
						</div>
					) : null}
				</FormSection>

				<div className="flex items-center justify-end gap-2 border-t bg-muted/30 px-5 py-3">
					<Button
						type="submit"
						disabled={pending || hasMismatchedTools}
						data-testid="save-agent-button"
					>
						{pending ? (
							<>
								<Loader2 className="h-3.5 w-3.5 animate-spin" />
								Saving…
							</>
						) : mode === "create" ? (
							"Create agent"
						) : (
							"Save changes"
						)}
					</Button>
				</div>
			</form>
		</Form>
	);
}


/**
 * AgentMCPConnectionsPanel — per-agent MCP connection grants.
 *
 * Lists every MCP connection in the agent's org with a checkbox. Each
 * checkbox is the grant: ticked means the agent may call any tool that
 * connection publishes; unticked means deny. Tools published by the
 * connection are listed below the checkbox as muted hints so the admin
 * can see what they're granting access to.
 *
 * New agents start with zero grants (deny-by-default). The migration
 * that introduced the join table backfilled grants for agents that
 * existed before the rollout, preserving the legacy "every agent in the
 * org auto-receives every connection's tools" behavior on upgrade.
 *
 * Hidden entirely when the agent has no organization_id — MCP
 * connections are strictly per-org so platform-level agents have
 * nothing to grant.
 */
function AgentMCPConnectionsPanel({
	organizationId,
	value,
	onChange,
}: {
	organizationId: string | null;
	value: string[];
	onChange: (next: string[]) => void;
}) {
	// Platform-level agents (no org) cannot carry MCP grants — connections
	// are per-org. Hide the panel entirely rather than show an always-empty
	// list that would confuse admins.
	if (!organizationId) {
		return null;
	}

	return (
		<AgentMCPConnectionsPanelInner
			organizationId={organizationId}
			value={value}
			onChange={onChange}
		/>
	);
}

function AgentMCPConnectionsPanelInner({
	organizationId,
	value,
	onChange,
}: {
	organizationId: string;
	value: string[];
	onChange: (next: string[]) => void;
}) {
	// Pull this org's connections + their tool catalogs. The list endpoint
	// returns summaries (no nested tools), so we follow up with
	// per-connection detail fetches. The list is small in practice (1–3
	// connections per org).
	const { data: connections = [] } = $api.useQuery(
		"get",
		"/api/mcp-connections",
		{ params: { query: { scope: organizationId } } },
	);
	const { data: servers = [] } = $api.useQuery(
		"get",
		"/api/mcp-servers",
		{ params: { query: { active_only: false } } },
	);

	const serverNameById = useMemo(() => {
		const map = new Map<string, string>();
		for (const s of servers) map.set(s.id, s.name);
		return map;
	}, [servers]);

	const connectionDetails = useQueries({
		queries: connections.map((c) =>
			$api.queryOptions("get", "/api/mcp-connections/{connection_id}", {
				params: { path: { connection_id: c.id } },
			}),
		),
	});

	const granted = useMemo(() => new Set(value), [value]);

	function toggle(connectionId: string, next: boolean) {
		if (next) {
			if (granted.has(connectionId)) return;
			onChange([...value, connectionId]);
		} else {
			onChange(value.filter((id) => id !== connectionId));
		}
	}

	if (connections.length === 0) {
		return (
			<div
				className="rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground"
				data-testid="agent-mcp-connections-panel-empty"
			>
				This organization has no MCP connections. Add one from the MCP
				servers admin page to grant agents access to external tools.
			</div>
		);
	}

	return (
		<div
			className="rounded-md border bg-muted/20 p-3 space-y-3"
			data-testid="agent-mcp-connections-panel"
		>
			<div>
				<div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
					External MCP tools
				</div>
				<p className="mt-1 text-xs text-muted-foreground">
					Check each connection this agent should be allowed to call.
					Unchecked connections are denied — the agent will not see any
					of their tools.
				</p>
			</div>
			<div className="space-y-3">
				{connections.map((conn, idx) => {
					const detail = connectionDetails[idx]?.data;
					const tools = detail?.tools ?? [];
					const enabledTools = tools.filter((t) => t.enabled);
					const serverName =
						serverNameById.get(conn.server_id) ?? "MCP server";
					const checked = granted.has(conn.id);
					return (
						<label
							key={conn.id}
							className="flex items-start gap-2 cursor-pointer"
							data-testid={`agent-mcp-connection-row-${conn.id}`}
						>
							<Checkbox
								checked={checked}
								onCheckedChange={(state) =>
									toggle(conn.id, state === true)
								}
								data-testid={`agent-mcp-connection-checkbox-${conn.id}`}
							/>
							<div className="flex-1">
								<div className="text-sm font-medium">
									{serverName}
								</div>
								{enabledTools.length === 0 ? (
									<div className="text-xs text-muted-foreground">
										No tools published yet.
									</div>
								) : (
									<div className="text-xs text-muted-foreground">
										Grants access to: {" "}
										{enabledTools
											.map((t) => t.tool_name)
											.join(", ")}
									</div>
								)}
							</div>
						</label>
					);
				})}
			</div>
		</div>
	);
}
