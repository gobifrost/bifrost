import { useState, useEffect, useCallback, useMemo } from "react";
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
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
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

interface CronValidationResult {
	valid: boolean;
	human_readable: string;
	next_runs?: string[];
	interval_seconds?: number;
	warning?: string;
	error?: string;
}

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

interface CreateEventSourceDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSuccess?: () => void;
}

function CreateEventSourceDialogContent({
	onOpenChange,
	onSuccess,
}: Omit<CreateEventSourceDialogProps, "open">) {
	const { isPlatformAdmin } = useAuth();
	const createMutation = useCreateEventSource();

	// Form state
	const [name, setName] = useState("");
	const [sourceType, setSourceType] = useState<EventSourceType>("webhook");
	const [organizationId, setOrganizationId] = useState<string | null>(null);
	const [adapterName, setAdapterName] = useState<string>("");
	const [integrationId, setIntegrationId] = useState<string>("");
	const [errors, setErrors] = useState<string[]>([]);

	// Topic state
	const [topicPickerValue, setTopicPickerValue] = useState<string>("");
	const [customTopic, setCustomTopic] = useState<string>("");
	const [topicError, setTopicError] = useState<string | null>(null);

	// Topic registry
	const { data: topicsData } = useTopics();
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

	// Schedule state
	const [cronExpression, setCronExpression] = useState("");
	const [timezone, setTimezone] = useState("UTC");
	const [overlapPolicy, setOverlapPolicy] = useState<
		"skip" | "queue" | "replace"
	>("skip");
	const [cronValidation, setCronValidation] =
		useState<CronValidationResult | null>(null);

	// Fetch available adapters
	const { data: adaptersData } = useWebhookAdapters();
	const adapters = adaptersData?.adapters || [];

	// Fetch integrations for OAuth-based adapters
	const { data: integrationsData } = useIntegrations();
	const integrations = integrationsData?.items || [];

	// Get selected adapter info
	const selectedAdapter = adapters.find((a) => a.name === adapterName);

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

	// Debounced cron validation
	const validateCronExpression = useCallback(async (expr: string) => {
		if (!expr.trim()) return;

		try {
			const response = await authFetch("/api/schedules/validate", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ expression: expr }),
			});
			const data = await response.json();
			setCronValidation(data);
		} catch {
			setCronValidation({
				valid: false,
				human_readable: "Failed to validate",
				error: "Unable to connect to validation service",
			});
		}
	}, []);

	useEffect(() => {
		if (!cronExpression) {
			return;
		}

		const timer = setTimeout(() => {
			validateCronExpression(cronExpression);
		}, 500);

		return () => clearTimeout(timer);
	}, [cronExpression, validateCronExpression]);

	// Computed display result - null when expression is empty
	const displayCronValidation = cronExpression ? cronValidation : null;

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

		if (selectedAdapter?.requires_integration && !integrationId) {
			newErrors.push(
				`This adapter requires a ${selectedAdapter.requires_integration} integration`,
			);
		}

		if (sourceType === "schedule") {
			if (!cronExpression.trim()) {
				newErrors.push(
					"Cron expression is required for schedule sources",
				);
			} else if (cronValidation && !cronValidation.valid) {
				newErrors.push(
					"Cron expression is invalid: " +
						(cronValidation.error || cronValidation.human_readable),
				);
			}
		}

		setErrors(newErrors);
		return newErrors.length === 0;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!validateForm()) return;

		try {
			const resolvedName =
				sourceType === "topic" && !name.trim()
					? topicToName(effectiveTopic)
					: name.trim();

			await createMutation.mutateAsync({
				body: {
					name: resolvedName,
					source_type: sourceType,
					organization_id: organizationId || undefined,
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
			toast.error("Failed to create event source");
		}
	};

	return (
		<form onSubmit={handleSubmit}>
			<DialogHeader>
				<div className="flex items-center gap-2">
					<DialogTitle>Create Event Source</DialogTitle>
					<EventTopicReferencePanel topics={curatedTopics} />
				</div>
				<DialogDescription>
					Create a new event source to receive webhooks, run on a
					schedule, or trigger workflows.
				</DialogDescription>
			</DialogHeader>

			<div className="space-y-4 py-4">
				{errors.length > 0 && (
					<Alert
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

				{/* Organization (Platform Admin Only) */}
				{isPlatformAdmin && (
					<div className="space-y-2">
						<Label htmlFor="organization">Organization</Label>
						<OrganizationSelect
							value={organizationId}
							onChange={(value) =>
								setOrganizationId(value ?? null)
							}
							showGlobal
						/>
						<p className="text-xs text-muted-foreground">
							Leave as Global to make this source available to all
							organizations.
						</p>
					</div>
				)}

				{/* Name */}
				<div className="space-y-2">
					<Label htmlFor="name">Name</Label>
					<Input
						id="name"
						value={name}
						onChange={(e) => setName(e.target.value)}
						placeholder={
							sourceType === "schedule"
								? "e.g., Daily Sync Schedule"
								: "e.g., GitHub Webhooks"
						}
					/>
				</div>

				{/* Source Type */}
				<div className="space-y-2">
					<Label htmlFor="source-type">Source Type</Label>
					<Select
						value={sourceType}
						onValueChange={(value) => {
							setSourceType(value as EventSourceType);
							setTopicPickerValue("");
							setCustomTopic("");
							setTopicError(null);
						}}
					>
						<SelectTrigger id="source-type">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="webhook">Webhook</SelectItem>
							<SelectItem value="schedule">Schedule</SelectItem>
							<SelectItem value="topic">Topic</SelectItem>
						</SelectContent>
					</Select>
				</div>

				{/* Topic Configuration */}
				{sourceType === "topic" && (
					<>
						<div className="border-t pt-4">
							<div className="mb-3">
								<h4 className="text-sm font-medium">
									Topic Configuration
								</h4>
							</div>
						</div>

						<div className="space-y-2">
							<Label htmlFor="topic-picker">Topic</Label>
							<Select
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
								<SelectTrigger id="topic-picker">
									<SelectValue placeholder="Select or enter a topic..." />
								</SelectTrigger>
								<SelectContent>
									{allKnownTopics.length > 0 && (
										<>
											<SelectGroup>
												<SelectLabel>
													Known Topics
												</SelectLabel>
												{allKnownTopics.map((topic) => (
													<SelectItem
														key={topic}
														value={topic}
													>
														{topic}
													</SelectItem>
												))}
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
									id="custom-topic"
									value={customTopic}
									onChange={(e) => {
										setCustomTopic(e.target.value);
										setTopicError(
											validateTopicClient(e.target.value),
										);
									}}
									placeholder="e.g. acme.deal_won"
									className="font-mono"
									aria-label="Custom topic"
								/>
							)}
							{topicError && (
								<p className="text-xs text-destructive">
									{topicError}
								</p>
							)}
							<p className="text-xs text-muted-foreground">
								Lowercase, dot-separated (e.g.{" "}
								<code>user.invited</code>). Must contain at
								least one dot.
							</p>
						</div>
					</>
				)}

				{/* Webhook Adapter */}
				{sourceType === "webhook" && (
					<div className="space-y-2">
						<Label htmlFor="adapter">Webhook Adapter</Label>
						<Select
							value={adapterName}
							onValueChange={handleAdapterChange}
						>
							<SelectTrigger id="adapter">
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
							<p className="text-xs text-muted-foreground">
								{selectedAdapter.description}
							</p>
						)}
					</div>
				)}

				{/* Integration (if required by adapter) */}
				{selectedAdapter?.requires_integration && (
					<div className="space-y-2">
						<Label htmlFor="integration">Integration</Label>
						<Select
							value={integrationId}
							onValueChange={setIntegrationId}
						>
							<SelectTrigger id="integration">
								<SelectValue placeholder="Select an integration..." />
							</SelectTrigger>
							<SelectContent>
								{filteredIntegrations.map((integration) => (
									<SelectItem
										key={integration.id}
										value={integration.id}
									>
										{integration.name}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<p className="text-xs text-muted-foreground">
							This adapter requires a{" "}
							{selectedAdapter.requires_integration} integration
							for authentication.
						</p>
					</div>
				)}

				{/* Dynamic Config Form - For adapters with config_schema */}
				{sourceType === "webhook" &&
					hasDynamicConfig &&
					selectedAdapter && (
						<>
							<div className="border-t pt-4">
								<h4 className="text-sm font-medium mb-3">
									Configuration
								</h4>
							</div>
							<DynamicConfigForm
								adapterName={selectedAdapter.name}
								integrationId={integrationId || undefined}
								configSchema={
									selectedAdapter.config_schema as unknown as ConfigSchema
								}
								config={webhookConfig}
								onChange={setWebhookConfig}
							/>
						</>
					)}

				{/* Rate Limiting */}
				{sourceType === "webhook" && (
					<>
						<div className="border-t pt-4">
							<h4 className="text-sm font-medium mb-3">
								Rate limiting
							</h4>
						</div>

						<div className="space-y-2">
							<Label htmlFor="rate-limit-per-minute">
								Max events
							</Label>
							<Input
								id="rate-limit-per-minute"
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
							<p className="text-xs text-muted-foreground">
								Maximum events accepted within the window below.
								Leave empty to disable the limit.
							</p>
						</div>

						<div className="space-y-2">
							<Label htmlFor="rate-limit-window">
								Per (seconds)
							</Label>
							<Input
								id="rate-limit-window"
								type="number"
								min={1}
								value={rateLimitWindowSeconds}
								onChange={(e) =>
									setRateLimitWindowSeconds(
										Number(e.target.value),
									)
								}
							/>
							<p className="text-xs text-muted-foreground">
								Window duration. Default 60 means the limit
								above applies per minute.
							</p>
						</div>

						<div className="flex items-center justify-between">
							<div className="space-y-0.5">
								<Label htmlFor="rate-limit-enabled">
									Enabled
								</Label>
								<p className="text-xs text-muted-foreground">
									Disable to bypass rate limiting for this
									source.
								</p>
							</div>
							<Switch
								id="rate-limit-enabled"
								checked={rateLimitEnabled}
								onCheckedChange={setRateLimitEnabled}
							/>
						</div>
					</>
				)}

				{/* Schedule Configuration */}
				{sourceType === "schedule" && (
					<>
						<div className="border-t pt-4">
							<h4 className="text-sm font-medium mb-3">
								Schedule Configuration
							</h4>
						</div>

						{/* Cron Expression */}
						<div className="space-y-2">
							<Label htmlFor="cron-expression">
								Cron Expression
							</Label>
							<Input
								id="cron-expression"
								value={cronExpression}
								onChange={(e) =>
									setCronExpression(e.target.value)
								}
								placeholder="0 9 * * *"
								className="font-mono"
							/>
							<p className="text-xs text-muted-foreground">
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
									className="text-xs"
								>
									{preset.label}
								</Button>
							))}
						</div>

						{/* Validation Result */}
						{displayCronValidation && (
							<div className="space-y-2">
								{displayCronValidation.valid ? (
									<Alert className="bg-green-50 border-green-200 dark:bg-green-950 dark:border-green-800">
										<CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400" />
										<AlertDescription className="text-green-800 dark:text-green-200">
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
									<Alert className="bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800">
										<AlertCircle className="h-4 w-4 text-yellow-600 dark:text-yellow-400" />
										<AlertDescription className="text-yellow-800 dark:text-yellow-200">
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
														const date = new Date(
															run,
														);
														return (
															<div
																key={i}
																className="text-xs flex items-center gap-2"
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
						<div className="space-y-2">
							<Label htmlFor="timezone">Timezone</Label>
							<Select
								value={timezone}
								onValueChange={setTimezone}
							>
								<SelectTrigger id="timezone">
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
							<p className="text-xs text-muted-foreground">
								The timezone used to evaluate the cron
								expression.
							</p>
						</div>

						{/* Overlap Policy */}
						<div className="space-y-2">
							<Label htmlFor="overlap-policy">
								Overlap policy
							</Label>
							<Select
								value={overlapPolicy}
								onValueChange={(v) =>
									setOverlapPolicy(
										v as "skip" | "queue" | "replace",
									)
								}
							>
								<SelectTrigger id="overlap-policy">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="skip">Skip</SelectItem>
									<SelectItem value="queue">Queue</SelectItem>
									<SelectItem value="replace">
										Replace
									</SelectItem>
								</SelectContent>
							</Select>
							<p className="text-xs text-muted-foreground">
								Skip (default) drops the new run if a previous
								run is still active. Queue and replace are
								reserved for future use.
							</p>
						</div>
					</>
				)}
			</div>

			<DialogFooter>
				<Button
					type="button"
					variant="outline"
					onClick={() => onOpenChange(false)}
				>
					Cancel
				</Button>
				<Button type="submit" disabled={isLoading}>
					{isLoading && (
						<Loader2 className="mr-2 h-4 w-4 animate-spin" />
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
}: CreateEventSourceDialogProps) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[500px]">
				{open && (
					<CreateEventSourceDialogContent
						onOpenChange={onOpenChange}
						onSuccess={onSuccess}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
