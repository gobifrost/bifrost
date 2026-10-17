import { EventSourceOptionsStatus } from "./EventSourceOptionsStatus";
import {
	useState,
	useRef,
	useEffect,
	useId,
	useMemo,
	type ReactNode,
} from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectSeparator,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle, CheckCircle2, ChevronDown, Loader2 } from "lucide-react";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import {
	useCreateEventSource,
	useWebhookAdapters,
	useTopics,
	type EventSourceType,
} from "@/services/events";
import { useIntegrations } from "@/services/integrations";
import { DynamicConfigForm, type ConfigSchema } from "./DynamicConfigForm";
import { EventTopicReferencePanel } from "./EventTopicReferencePanel";
import { authFetch } from "@/lib/api-client";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";

import type { components } from "@/lib/v1";
type CronValidationResult = components["schemas"]["CronValidationResponse"];

const TOPIC_REGEX = /^[a-z0-9_.]+$/;
const TOPIC_MAX_LEN = 100;

function validateTopicClient(topic: string): string | null {
	if (!topic) return "Topic is required";
	if (topic.length > TOPIC_MAX_LEN)
		return `Topic must be at most ${TOPIC_MAX_LEN} characters`;
	if (!TOPIC_REGEX.test(topic))
		return "Topic must match ^[a-z0-9_.]+$ (lowercase, digits, dots, underscores)";
	if (!topic.includes("."))
		return "Topic must contain at least one dot (e.g. 'user.invited')";
	return null;
}

function topicToName(topic: string): string {
	return topic
		.split(".")
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
}

const CUSTOM_TOPIC_VALUE = "__custom__";

const CRON_PRESETS = [
	{ label: "Every 5 min", expression: "*/5 * * * *" },
	{ label: "Hourly", expression: "0 * * * *" },
	{ label: "Daily 9 AM", expression: "0 9 * * *" },
	{ label: "Weekly Mon", expression: "0 0 * * 1" },
];

const COMMON_TIMEZONES = [
	"UTC",
	"America/New_York",
	"America/Chicago",
	"America/Denver",
	"America/Los_Angeles",
	"America/Phoenix",
	"Europe/London",
	"Europe/Paris",
	"Europe/Berlin",
	"Asia/Tokyo",
	"Asia/Shanghai",
	"Australia/Sydney",
	"Pacific/Auckland",
];

function FormSection({
	title,
	description,
	children,
}: {
	title: string;
	description?: string;
	children: ReactNode;
}) {
	return (
		<section className="min-w-0 space-y-4 border-t pt-5 first:border-0 first:pt-0">
			<div className="space-y-1">
				<h3 className="text-sm font-semibold">{title}</h3>
				{description && (
					<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
						{description}
					</p>
				)}
			</div>
			<div className="space-y-4">{children}</div>
		</section>
	);
}

interface CreateEventSourceDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSuccess?: () => void;
	knownOrganizations?: readonly { id: string; name: string }[];
}

function CreateEventSourceDialogContent({
	onOpenChange,
	onSuccess,
	createMutation,
	knownOrganizations,
}: Omit<CreateEventSourceDialogProps, "open"> & {
	createMutation: ReturnType<typeof useCreateEventSource>;
}) {
	const authorization = useAuthorization();
	const canCreateAnySource = authorization.canAnywhere("events.readwrite");
	const formId = useId();

	// Form state
	const [name, setName] = useState("");
	const [sourceType, setSourceType] = useState<EventSourceType>("webhook");
	const [organizationId, setOrganizationId] = useState<string | null>(null);
	const [adapterName, setAdapterName] = useState<string>("");
	const [integrationId, setIntegrationId] = useState<string>("");
	const [errors, setErrors] = useState<string[]>([]);
	const errorSummaryRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (errors.length) errorSummaryRef.current?.focus();
	}, [errors]);

	// Topic state
	const [topicPickerValue, setTopicPickerValue] = useState<string>("");
	const [customTopic, setCustomTopic] = useState<string>("");
	const [topicError, setTopicError] = useState<string | null>(null);

	// Topic registry
	const {
		data: topicsData,
		isError: topicsError,
		isFetching: topicsFetching,
		refetch: refetchTopics,
	} = useTopics();
	const curatedTopics = topicsData?.curated ?? [];
	const inUseTopics = topicsData?.in_use ?? [];
	const allKnownTopics = [
		...curatedTopics.map((t) => t.topic),
		...inUseTopics.filter((t) => !curatedTopics.some((c) => c.topic === t)),
	];

	const effectiveTopic =
		topicPickerValue === CUSTOM_TOPIC_VALUE
			? customTopic
			: topicPickerValue;

	// Dynamic config for adapters with config_schema
	const [webhookConfig, setWebhookConfig] = useState<Record<string, unknown>>(
		{},
	);

	// Webhook rate-limit state
	const [rateLimitPerMinute, setRateLimitPerMinute] = useState<number | null>(
		60,
	);
	const [rateLimitWindowSeconds, setRateLimitWindowSeconds] = useState(60);
	const [rateLimitEnabled, setRateLimitEnabled] = useState(true);
	const [advancedOpen, setAdvancedOpen] = useState(false);

	// Schedule state
	const [cronExpression, setCronExpression] = useState("");
	const [timezone, setTimezone] = useState("UTC");
	const [overlapPolicy, setOverlapPolicy] = useState<
		"skip" | "queue" | "replace"
	>("skip");
	const [cronValidation, setCronValidation] = useState<{
		expression: string;
		timezone: string;
		result: CronValidationResult;
	} | null>(null);

	// Fetch available adapters
	const {
		data: adaptersData,
		isError: adaptersError,
		isFetching: adaptersFetching,
		refetch: refetchAdapters,
	} = useWebhookAdapters();
	const adapters = adaptersData?.adapters || [];

	// Fetch integrations for OAuth-based adapters
	const {
		data: integrationsData,
		isError: integrationsError,
		isFetching: integrationsFetching,
		refetch: refetchIntegrations,
	} = useIntegrations();
	const integrations = integrationsData?.items || [];

	// Get selected adapter info
	const selectedAdapter = adapters.find((a) => a.name === adapterName);
	const selectedAdapterRequiresOrganization = Boolean(
		selectedAdapter?.requires_organization,
	);

	// Filter integrations if adapter requires specific OAuth
	const filteredIntegrations = selectedAdapter?.requires_integration
		? integrations.filter(
				(i) => i.name === selectedAdapter.requires_integration,
			)
		: integrations;

	// Check if adapter has dynamic config schema (non-empty properties)
	const hasDynamicConfig = useMemo(() => {
		if (!selectedAdapter?.config_schema) return false;
		const schema = selectedAdapter.config_schema as {
			properties?: Record<string, unknown>;
		};
		return schema.properties && Object.keys(schema.properties).length > 0;
	}, [selectedAdapter]);

	// Reset config when adapter changes
	const handleAdapterChange = (newAdapter: string) => {
		setAdapterName(newAdapter);
		setWebhookConfig({});
		setIntegrationId("");
	};

	// Ignore and cancel validation for a previous expression or timezone.
	useEffect(() => {
		const expression = cronExpression.trim();
		if (sourceType !== "schedule" || !expression) return;
		let active = true;
		const controller = new AbortController();
		const timer = setTimeout(async () => {
			try {
				const response = await authFetch("/api/schedules/validate", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ expression, timezone }),
					signal: controller.signal,
				});
				if (!response.ok) throw new Error("Validation request failed");
				const result: CronValidationResult = await response.json();
				if (active) setCronValidation({ expression, timezone, result });
			} catch {
				if (active)
					setCronValidation({
						expression,
						timezone,
						result: {
							valid: false,
							human_readable: "Could not validate schedule",
							error: "Unable to connect to the validation service",
						},
					});
			}
		}, 500);
		return () => {
			active = false;
			clearTimeout(timer);
			controller.abort();
		};
	}, [cronExpression, timezone, sourceType]);

	const displayCronValidation =
		cronValidation?.expression === cronExpression.trim() &&
		cronValidation.timezone === timezone
			? cronValidation.result
			: null;

	const isLoading = createMutation.isPending;

	const validateForm = (): boolean => {
		const newErrors: string[] = [];

		if (sourceType === "topic") {
			const err = validateTopicClient(effectiveTopic);
			if (err) {
				newErrors.push(err);
				setTopicError(err);
			}
			// Name defaults from topic if blank — no error needed
		} else {
			if (!name.trim()) {
				newErrors.push("Name is required");
			}
		}

		if (sourceType === "webhook" && !adapterName) {
			newErrors.push("Please select a webhook adapter");
		}

		if (
			sourceType === "webhook" &&
			selectedAdapter?.requires_integration &&
			!integrationId
		) {
			newErrors.push(
				`This adapter requires a ${selectedAdapter.requires_integration} integration`,
			);
		}

		if (
			sourceType === "webhook" &&
			selectedAdapterRequiresOrganization &&
			canCreateAnySource &&
			!organizationId
		) {
			newErrors.push("Please select an organization for this adapter");
		}
		if (
			canCreateAnySource &&
			!authorization.canAt(
				"events.readwrite",
				orgTarget(organizationId),
			)
		) {
			newErrors.push(
				"Select an organization where you can create event sources",
			);
		}

		if (sourceType === "schedule") {
			if (!cronExpression.trim()) {
				newErrors.push(
					"Cron expression is required for schedule sources",
				);
			} else if (displayCronValidation && !displayCronValidation.valid) {
				newErrors.push(
					"Cron expression is invalid: " +
						(displayCronValidation.error ||
							displayCronValidation.human_readable),
				);
			}
		}

		setErrors(newErrors);
		return newErrors.length === 0;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (isLoading || !validateForm()) return;

		try {
			const resolvedName =
				sourceType === "topic" && !name.trim()
					? topicToName(effectiveTopic)
					: name.trim();

			await createMutation.mutateAsync({
				body: {
					name: resolvedName,
					source_type: sourceType,
						organization_id: organizationId,
					event_type:
						sourceType === "topic" ? effectiveTopic : undefined,
					webhook:
						sourceType === "webhook"
							? {
									adapter_name: adapterName || undefined,
									integration_id: integrationId || undefined,
									config: webhookConfig,
									rate_limit_per_minute: rateLimitPerMinute,
									rate_limit_window_seconds:
										rateLimitWindowSeconds,
									rate_limit_enabled: rateLimitEnabled,
								}
							: undefined,
					schedule:
						sourceType === "schedule"
							? {
									cron_expression: cronExpression.trim(),
									timezone,
									enabled: true,
									overlap_policy: overlapPolicy,
								}
							: undefined,
				},
			});

			toast.success("Event source created successfully");
			onOpenChange(false);
			onSuccess?.();
		} catch (error) {
			console.error("Failed to create event source:", error);
			setErrors([
				"Could not create this source. Your settings are still here. Try again.",
			]);
		}
	};

	return (
		<form
			onSubmit={handleSubmit}
			className="flex max-h-[calc(90dvh-3rem)] min-h-0 min-w-0 flex-col"
		>
			<DialogHeader className="shrink-0 border-b pb-4">
				<div className="flex min-w-0 items-center gap-2">
					<DialogTitle className="min-w-0 flex-1">
						Create Event Source
					</DialogTitle>
					<EventTopicReferencePanel topics={curatedTopics} />
				</div>
				<DialogDescription>
					Create a new event source to receive webhooks, run on a
					schedule, or trigger workflows.
				</DialogDescription>
			</DialogHeader>

			<div className="-mx-1 min-h-0 min-w-0 overflow-y-auto px-1">
				<fieldset
					disabled={isLoading}
					className="min-w-0 space-y-5 py-5"
				>
					{errors.length > 0 && (
						<Alert
							ref={errorSummaryRef}
							tabIndex={-1}
							className="focus:outline-none"
							variant="destructive"
							role="alert"
							aria-live="polite"
						>
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>
								<ul className="list-disc list-inside">
									{errors.map((error, i) => (
										<li key={i}>{error}</li>
									))}
								</ul>
							</AlertDescription>
						</Alert>
					)}

					<FormSection
						title="Scope"
						description="Choose where this event source is available."
					>
							{canCreateAnySource && (
							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-organization`}>
									Organization
								</Label>
								<OrganizationSelect
									id={`${formId}-organization`}
									disabled={isLoading}
									triggerClassName="min-h-11 lg:min-h-11"
									value={organizationId}
									onChange={(value) =>
										setOrganizationId(value ?? null)
									}
									showGlobal={authorization.canAt(
										"events.readwrite",
										GLOBAL_TARGET,
									)}
									knownOrganizations={knownOrganizations}
									loadOrganizations={authorization.canAnywhere(
										"organizations.read",
									)}
									filterOrganizations={(organization) =>
										authorization.canAt(
											"events.readwrite",
											orgTarget(organization.id),
										)
									}
								/>
								<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
									Leave as Global to make this source
									available to all organizations.
								</p>
							</div>
						)}

						<div className="min-w-0 space-y-2">
							<Label htmlFor={`${formId}-name`}>Name</Label>
							<Input
								className="min-h-11"
								id={`${formId}-name`}
								value={name}
								onChange={(e) => setName(e.target.value)}
								placeholder={
									sourceType === "schedule"
										? "e.g., Daily Sync Schedule"
										: "e.g., GitHub Webhooks"
								}
							/>
						</div>
					</FormSection>

					<FormSection
						title="Trigger"
						description="Pick how Bifrost receives or creates events."
					>
						<div className="min-w-0 space-y-2">
							<Label htmlFor={`${formId}-source-type`}>
								Source Type
							</Label>
							<Select
								disabled={isLoading}
								value={sourceType}
								onValueChange={(value) => {
									setSourceType(value as EventSourceType);
									setTopicPickerValue("");
									setCustomTopic("");
									setTopicError(null);
								}}
							>
								<SelectTrigger
									id={`${formId}-source-type`}
									className="w-full min-h-11 data-[size=default]:h-auto [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="webhook">
										Webhook
									</SelectItem>
									<SelectItem value="schedule">
										Schedule
									</SelectItem>
									<SelectItem value="topic">Topic</SelectItem>
								</SelectContent>
							</Select>
						</div>
					</FormSection>

					{/* Topic Configuration */}
					{sourceType === "topic" && (
						<FormSection title="Topic Configuration">
							<EventSourceOptionsStatus
								label="topic suggestions"
								error={topicsError}
								pending={topicsFetching}
								hasData={!!topicsData}
								disabled={isLoading}
								onRetry={() => void refetchTopics()}
								hint="Enter a custom topic or retry. Your entries have been kept."
							/>
							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-topic-picker`}>
									Topic
								</Label>
								<Select
									disabled={isLoading}
									value={topicPickerValue}
									onValueChange={(value) => {
										setTopicPickerValue(value);
										setTopicError(null);
										if (
											value !== CUSTOM_TOPIC_VALUE &&
											!name.trim()
										) {
											setName(topicToName(value));
										}
									}}
								>
									<SelectTrigger
										id={`${formId}-topic-picker`}
										className="w-full min-h-11 data-[size=default]:h-auto [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
									>
										<SelectValue placeholder="Select or enter a topic..." />
									</SelectTrigger>
									<SelectContent>
										{allKnownTopics.length > 0 && (
											<>
												<SelectGroup>
													<SelectLabel>
														Known Topics
													</SelectLabel>
													{allKnownTopics.map(
														(topic) => (
															<SelectItem
																key={topic}
																value={topic}
															>
																{topic}
															</SelectItem>
														),
													)}
												</SelectGroup>
												<SelectSeparator />
											</>
										)}
										<SelectItem value={CUSTOM_TOPIC_VALUE}>
											Custom topic...
										</SelectItem>
									</SelectContent>
								</Select>
								{topicPickerValue === CUSTOM_TOPIC_VALUE && (
									<Input
										className="min-h-11 font-mono"
										id={`${formId}-custom-topic`}
										value={customTopic}
										onChange={(e) => {
											setCustomTopic(e.target.value);
											setTopicError(
												validateTopicClient(
													e.target.value,
												),
											);
										}}
										placeholder="e.g. acme.deal_won"

										aria-label="Custom topic"
									/>
								)}
								{topicError && (
									<p className="text-sm leading-6 [overflow-wrap:anywhere] text-destructive">
										{topicError}
									</p>
								)}
								<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
									Lowercase, dot-separated (e.g.{" "}
									<code>user.invited</code>). Must contain at
									least one dot.
								</p>
							</div>
						</FormSection>
					)}

					{/* Webhook Adapter */}
					{sourceType === "webhook" && (
						<FormSection
							title={
								selectedAdapter?.name === "microsoft_graph"
									? "Microsoft Graph Subscription"
									: "Webhook Subscription"
							}
							description={
								selectedAdapter?.name === "microsoft_graph"
									? "Connect the Microsoft tenant, then choose the Graph resource to subscribe to."
									: "Choose the adapter and connection details for incoming events."
							}
						>
							<EventSourceOptionsStatus
								label="webhook adapters"
								error={adaptersError}
								pending={adaptersFetching}
								hasData={!!adaptersData}
								disabled={isLoading}
								onRetry={() => void refetchAdapters()}
								emptyMessage={
									!adapters.length
										? "No webhook adapters are available."
										: undefined
								}
							/>
							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-adapter`}>
									Webhook Adapter
								</Label>
								<Select
									disabled={isLoading || !adapters.length}
									value={adapterName}
									onValueChange={handleAdapterChange}
								>
									<SelectTrigger
										id={`${formId}-adapter`}
										className="w-full min-h-11 data-[size=default]:h-auto [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
									>
										<SelectValue placeholder="Select an adapter..." />
									</SelectTrigger>
									<SelectContent>
										{adapters.map((adapter) => (
											<SelectItem
												key={adapter.name}
												value={adapter.name}
											>
												{adapter.display_name}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
								{selectedAdapter?.description && (
									<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
										{selectedAdapter.description}
									</p>
								)}
							</div>

							{selectedAdapter?.requires_integration && (
								<div className="min-w-0 space-y-2">
									<EventSourceOptionsStatus
										label="integrations"
										error={integrationsError}
										pending={integrationsFetching}
										hasData={!!integrationsData}
										disabled={isLoading}
										onRetry={() =>
											void refetchIntegrations()
										}
										emptyMessage={
											!filteredIntegrations.length
												? `No ${selectedAdapter.requires_integration} integration is available. Create one in Integrations, then refresh this list.`
												: undefined
										}
									/>
									<Label htmlFor={`${formId}-integration`}>
										Integration
									</Label>
									<Select
										disabled={
											isLoading ||
											!filteredIntegrations.length
										}
										value={integrationId}
										onValueChange={setIntegrationId}
									>
										<SelectTrigger
											id={`${formId}-integration`}
											className="w-full min-h-11 data-[size=default]:h-auto [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
										>
											<SelectValue placeholder="Select an integration..." />
										</SelectTrigger>
										<SelectContent>
											{filteredIntegrations.map(
												(integration) => (
													<SelectItem
														key={integration.id}
														value={integration.id}
													>
														{integration.name}
													</SelectItem>
												),
											)}
										</SelectContent>
									</Select>
									<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
										This adapter requires a{" "}
										{selectedAdapter.requires_integration}{" "}
										integration for authentication.
									</p>
								</div>
							)}

							{hasDynamicConfig && selectedAdapter && (
								<DynamicConfigForm
									adapterName={selectedAdapter.name}
									integrationId={integrationId || undefined}
									requiresIntegration={Boolean(
										selectedAdapter.requires_integration,
									)}
									organizationId={organizationId}
									configSchema={
										selectedAdapter.config_schema as unknown as ConfigSchema
									}
									config={webhookConfig}
									onChange={setWebhookConfig}
								/>
							)}
						</FormSection>
					)}

					{/* Rate Limiting */}
					{sourceType === "webhook" && (
						<Collapsible
							open={advancedOpen}
							onOpenChange={setAdvancedOpen}
							className="rounded-lg border"
						>
							<CollapsibleTrigger asChild>
								<Button
									type="button"
									variant="ghost"
									className="flex min-h-11 w-full items-center justify-between px-4 py-3 text-sm font-medium [&[data-state=open]>svg]:rotate-180"
								>
									Advanced
									<ChevronDown className="h-4 w-4 motion-safe:transition-transform" />
								</Button>
							</CollapsibleTrigger>
							<CollapsibleContent className="space-y-4 px-4 pb-4">
								<div className="space-y-1">
									<h3 className="text-sm font-semibold">
										Rate limiting
									</h3>
									<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
										Control how many events this source
										accepts before throttling.
									</p>
								</div>

								<div className="min-w-0 space-y-2">
									<Label
										htmlFor={`${formId}-rate-limit-per-minute`}
									>
										Max events
									</Label>
									<Input
										className="min-h-11"
										id={`${formId}-rate-limit-per-minute`}
										type="number"
										min={1}
										value={rateLimitPerMinute ?? ""}
										onChange={(e) => {
											const val = e.target.value;
											setRateLimitPerMinute(
												val === "" ? null : Number(val),
											);
										}}
										placeholder="60 (leave empty to disable)"
									/>
									<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
										Maximum events accepted within the
										window below. Leave empty to disable the
										limit.
									</p>
								</div>

								<div className="min-w-0 space-y-2">
									<Label
										htmlFor={`${formId}-rate-limit-window`}
									>
										Per (seconds)
									</Label>
									<Input
										className="min-h-11"
										id={`${formId}-rate-limit-window`}
										type="number"
										min={1}
										value={rateLimitWindowSeconds}
										onChange={(e) =>
											setRateLimitWindowSeconds(
												Number(e.target.value),
											)
										}
									/>
									<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
										Window duration. Default 60 means the
										limit above applies per minute.
									</p>
								</div>

								<div className="flex items-center justify-between gap-4">
									<div className="space-y-0.5">
										<Label
											className="min-h-11 flex items-center cursor-pointer"
											htmlFor={`${formId}-rate-limit-enabled`}
										>
											Enabled
										</Label>
										<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
											Disable to bypass rate limiting for
											this source.
										</p>
									</div>
									<Switch
										disabled={isLoading}
										className="shrink-0"
										id={`${formId}-rate-limit-enabled`}
										checked={rateLimitEnabled}
										onCheckedChange={setRateLimitEnabled}
									/>
								</div>
							</CollapsibleContent>
						</Collapsible>
					)}

					{/* Schedule Configuration */}
					{sourceType === "schedule" && (
						<FormSection title="Schedule Configuration">
							{/* Cron Expression */}
							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-cron-expression`}>
									Cron Expression
								</Label>
								<Input
									className="min-h-11 font-mono"
									id={`${formId}-cron-expression`}
									value={cronExpression}
									onChange={(e) =>
										setCronExpression(e.target.value)
									}
									placeholder="0 9 * * *"
								/>
								<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
									Standard 5-field cron: minute hour day month
									weekday
								</p>
							</div>

							{/* Quick Presets */}
							<div className="flex flex-wrap gap-2">
								{CRON_PRESETS.map((preset) => (
									<Button
										key={preset.expression}
										type="button"
										variant="outline"
										size="sm"
										onClick={() =>
											setCronExpression(preset.expression)
										}
										className="min-h-11 text-sm"
									>
										{preset.label}
									</Button>
								))}
							</div>

							{/* Validation Result */}
							{displayCronValidation && (
								<div className="min-w-0 space-y-2">
									{displayCronValidation.valid ? (
										<Alert className="bg-[var(--bf-success-soft)] border-transparent">
											<CheckCircle2 className="h-4 w-4 text-[var(--bf-success)]" />
											<AlertDescription className="text-[var(--bf-success)]">
												{
													displayCronValidation.human_readable
												}
											</AlertDescription>
										</Alert>
									) : (
										<Alert variant="destructive">
											<AlertCircle className="h-4 w-4" />
											<AlertDescription>
												{displayCronValidation.error ||
													displayCronValidation.human_readable}
											</AlertDescription>
										</Alert>
									)}

									{displayCronValidation.warning && (
										<Alert className="bg-[var(--bf-warning-soft)] border-transparent">
											<AlertCircle className="h-4 w-4 text-[var(--bf-warning)]" />
											<AlertDescription className="text-[var(--bf-warning)]">
												{displayCronValidation.warning}
											</AlertDescription>
										</Alert>
									)}

									{displayCronValidation.next_runs &&
										displayCronValidation.next_runs.length >
											0 && (
											<div>
												<h4 className="text-sm font-semibold mb-1">
													Next runs:
												</h4>
												<div className="space-y-0.5">
													{displayCronValidation.next_runs.map(
														(run, i) => {
															const date =
																new Date(run);
															return (
																<div
																	key={i}
																	className="text-sm leading-6 [overflow-wrap:anywhere] flex flex-wrap items-center gap-2"
																>
																	<span className="text-muted-foreground">
																		-
																	</span>
																	<span>
																		{date.toLocaleString()}
																	</span>
																	<span className="text-muted-foreground">
																		(
																		{formatDistanceToNow(
																			date,
																			{
																				addSuffix: true,
																			},
																		)}
																		)
																	</span>
																</div>
															);
														},
													)}
												</div>
											</div>
										)}
								</div>
							)}

							{/* Timezone */}
							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-timezone`}>
									Timezone
								</Label>
								<Select
									disabled={isLoading}
									value={timezone}
									onValueChange={setTimezone}
								>
									<SelectTrigger
										id={`${formId}-timezone`}
										className="w-full min-h-11 data-[size=default]:h-auto [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{COMMON_TIMEZONES.map((tz) => (
											<SelectItem key={tz} value={tz}>
												{tz.replace(/_/g, " ")}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
								<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
									The timezone used to evaluate the cron
									expression.
								</p>
							</div>

							{/* Overlap Policy */}
							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-overlap-policy`}>
									Overlap policy
								</Label>
								<Select
									disabled={isLoading}
									value={overlapPolicy}
									onValueChange={(v) =>
										setOverlapPolicy(
											v as "skip" | "queue" | "replace",
										)
									}
								>
									<SelectTrigger
										id={`${formId}-overlap-policy`}
										className="w-full min-h-11 data-[size=default]:h-auto [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value="skip">
											Skip
										</SelectItem>
										<SelectItem value="queue">
											Queue
										</SelectItem>
										<SelectItem value="replace">
											Replace
										</SelectItem>
									</SelectContent>
								</Select>
								<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
									Skip (default) drops the new run if a
									previous run is still active. Queue and
									replace are reserved for future use.
								</p>
							</div>
						</FormSection>
					)}
				</fieldset>
			</div>

			<DialogFooter className="shrink-0 border-t pt-4">
				<Button
					type="button"
					variant="outline"
					disabled={isLoading}
					className="min-h-11"
					onClick={() => onOpenChange(false)}
				>
					Cancel
				</Button>
				<Button type="submit" className="min-h-11" disabled={isLoading}>
					{isLoading && (
						<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
					)}
					Create Event Source
				</Button>
			</DialogFooter>
		</form>
	);
}

export function CreateEventSourceDialog({
	open,
	onOpenChange,
	onSuccess,
	knownOrganizations,
}: CreateEventSourceDialogProps) {
	const createMutation = useCreateEventSource();
	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!createMutation.isPending) onOpenChange(next);
			}}
		>
			<DialogContent className="overflow-hidden sm:max-w-[500px]">
				{open && (
					<CreateEventSourceDialogContent
						createMutation={createMutation}
						onOpenChange={onOpenChange}
						onSuccess={onSuccess}
						knownOrganizations={knownOrganizations}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
