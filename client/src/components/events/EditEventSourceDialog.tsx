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
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import {
	useUpdateEventSource,
	useWebhookAdapters,
	type EventSource,
} from "@/services/events";
import { DynamicConfigForm, type ConfigSchema } from "./DynamicConfigForm";
import { authFetch } from "@/lib/api-client";

interface CronValidationResult {
	valid: boolean;
	human_readable: string;
	next_runs?: string[];
	interval_seconds?: number;
	warning?: string;
	error?: string;
}

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

interface EditEventSourceDialogProps {
	source: EventSource | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

function EditEventSourceDialogContent({
	source,
	onOpenChange,
}: {
	source: EventSource;
	onOpenChange: (open: boolean) => void;
}) {
	const { isPlatformAdmin } = useAuth();
	const updateMutation = useUpdateEventSource();

	// Fetch adapter metadata for dynamic config
	const { data: adaptersData } = useWebhookAdapters();
	const adapters = adaptersData?.adapters || [];
	const selectedAdapter = adapters.find(
		(a) => a.name === source.webhook?.adapter_name,
	);
	const hasDynamicConfig = useMemo(() => {
		if (!selectedAdapter?.config_schema) return false;
		const schema = selectedAdapter.config_schema as {
			properties?: Record<string, unknown>;
		};
		return schema.properties && Object.keys(schema.properties).length > 0;
	}, [selectedAdapter]);

	// Form state - initialized from props, component remounts when dialog opens
	const [name, setName] = useState(source.name);
	const [organizationId, setOrganizationId] = useState<string | null>(
		source.organization_id ?? null,
	);

	// Webhook config (unified for all adapters)
	const [webhookConfig, setWebhookConfig] = useState<
		Record<string, unknown>
	>(source.webhook?.config || {});

	// Webhook rate-limit config
	const [rateLimitPerMinute, setRateLimitPerMinute] = useState<number | null>(
		source.webhook?.rate_limit_per_minute ?? 60,
	);
	const [rateLimitWindowSeconds, setRateLimitWindowSeconds] = useState(
		source.webhook?.rate_limit_window_seconds ?? 60,
	);
	const [rateLimitEnabled, setRateLimitEnabled] = useState(
		source.webhook?.rate_limit_enabled ?? true,
	);

	// Schedule config fields
	const [cronExpression, setCronExpression] = useState<string>(
		source.schedule?.cron_expression ?? "",
	);
	const [timezone, setTimezone] = useState<string>(
		source.schedule?.timezone ?? "UTC",
	);
	const [scheduleEnabled, setScheduleEnabled] = useState<boolean>(
		source.schedule?.enabled ?? true,
	);
	const [overlapPolicy, setOverlapPolicy] = useState<
		"skip" | "queue" | "replace"
	>(source.schedule?.overlap_policy ?? "skip");

	// Cron validation state
	const [cronValidation, setCronValidation] =
		useState<CronValidationResult | null>(null);

	const [errors, setErrors] = useState<string[]>([]);

	const isLoading = updateMutation.isPending;
	const isWebhook = source.source_type === "webhook";
	const isSchedule = source.source_type === "schedule";

	// Debounced cron validation
	const validateCron = useCallback(async (expr: string) => {
		if (!expr.trim()) {
			setCronValidation(null);
			return;
		}

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
		if (!cronExpression.trim()) {
			return;
		}

		const timer = setTimeout(() => {
			validateCron(cronExpression);
		}, 500);

		return () => clearTimeout(timer);
	}, [cronExpression, validateCron]);

	// Computed display result - null when expression is empty
	const displayCronValidation = cronExpression.trim()
		? cronValidation
		: null;

	const validateForm = (): boolean => {
		const newErrors: string[] = [];

		if (!name.trim()) {
			newErrors.push("Name is required");
		}

		if (isSchedule) {
			if (!cronExpression.trim()) {
				newErrors.push("Cron expression is required");
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
			// Build body - include organization_id if admin changed it
			const body: Record<string, unknown> = {
				name: name.trim(),
				webhook: isWebhook
					? {
							config: webhookConfig,
							rate_limit_per_minute: rateLimitPerMinute,
							rate_limit_window_seconds: rateLimitWindowSeconds,
							rate_limit_enabled: rateLimitEnabled,
						}
					: undefined,
				schedule: isSchedule
					? {
							cron_expression: cronExpression.trim(),
							timezone,
							enabled: scheduleEnabled,
							overlap_policy: overlapPolicy,
						}
					: undefined,
			};
			if (isPlatformAdmin) {
				body.organization_id = organizationId ?? null;
			}

			await updateMutation.mutateAsync({
				params: {
					path: { source_id: source.id },
				},
				body: body as NonNullable<typeof updateMutation.variables>["body"],
			});

			toast.success("Event source updated");
			onOpenChange(false);
		} catch (error) {
			console.error("Failed to update event source:", error);
			toast.error("Failed to update event source");
		}
	};

	return (
		<form onSubmit={handleSubmit}>
			<DialogHeader>
				<DialogTitle>Edit Event Source</DialogTitle>
				<DialogDescription>
					Update the event source settings.
				</DialogDescription>
			</DialogHeader>

			<div className="space-y-4 py-4">
				{errors.length > 0 && (
					<Alert variant="destructive">
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
						placeholder="e.g., GitHub Webhooks"
					/>
				</div>

				{/* Topic (read-only) */}
				{source.source_type === "topic" && source.event_type && (
					<div className="space-y-2">
						<Label htmlFor="topic">Topic</Label>
						<Input
							id="topic"
							value={source.event_type}
							disabled
							className="font-mono"
						/>
						<p className="text-xs text-muted-foreground">
							The topic this source publishes to. Cannot be changed after creation.
						</p>
					</div>
				)}

				{/* Webhook Config (Dynamic from adapter schema) */}
				{isWebhook && hasDynamicConfig && selectedAdapter && (
					<>
						<div className="border-t pt-4">
							<h4 className="text-sm font-medium mb-3">
								Webhook Configuration
							</h4>
							{selectedAdapter.display_name !== "Generic Webhook" && (
								<p className="text-xs text-muted-foreground mb-3">
									Adapter: {selectedAdapter.display_name}
								</p>
							)}
						</div>
						<DynamicConfigForm
							adapterName={selectedAdapter.name}
							configSchema={
								selectedAdapter.config_schema as unknown as ConfigSchema
							}
							config={webhookConfig}
							onChange={setWebhookConfig}
						/>
					</>
				)}

				{/* Rate Limiting (Webhook only) */}
				{isWebhook && (
					<>
						<div className="border-t pt-4">
							<h4 className="text-sm font-medium mb-3">
								Rate limiting
							</h4>
						</div>

						{source.webhook && source.webhook.rate_limited_count_24h > 0 && (
							<p className="text-xs text-destructive">
								{source.webhook.rate_limited_count_24h} request
								{source.webhook.rate_limited_count_24h === 1 ? "" : "s"}{" "}
								rate limited in the last 24 hours.
							</p>
						)}

						<div className="space-y-2">
							<Label htmlFor="rate-limit-per-minute">Max events</Label>
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
									setRateLimitWindowSeconds(Number(e.target.value))
								}
							/>
							<p className="text-xs text-muted-foreground">
								Window duration. Default 60 means the limit above
								applies per minute.
							</p>
						</div>

						<div className="flex items-center justify-between">
							<div className="space-y-0.5">
								<Label htmlFor="rate-limit-enabled">
									Enabled
								</Label>
								<p className="text-xs text-muted-foreground">
									Disable to bypass rate limiting for this source.
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

				{/* Schedule Config */}
				{isSchedule && (
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
								placeholder="e.g., 0 9 * * * (daily at 9 AM)"
								className="font-mono"
							/>
							<p className="text-xs text-muted-foreground">
								Standard 5-field cron expression (minute hour
								day-of-month month day-of-week)
							</p>

							{/* Cron Validation Display */}
							{displayCronValidation && (
								<div className="mt-2">
									{displayCronValidation.valid ? (
										<Alert className="bg-green-50 border-green-200 dark:bg-green-950 dark:border-green-800">
											<CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400" />
											<AlertDescription className="text-green-800 dark:text-green-200">
												{displayCronValidation.human_readable}
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
										<Alert className="mt-2 bg-yellow-50 border-yellow-200 dark:bg-yellow-950 dark:border-yellow-800">
											<AlertCircle className="h-4 w-4 text-yellow-600 dark:text-yellow-400" />
											<AlertDescription className="text-yellow-800 dark:text-yellow-200">
												{displayCronValidation.warning}
											</AlertDescription>
										</Alert>
									)}
								</div>
							)}
						</div>

						{/* Timezone */}
						<div className="space-y-2">
							<Label htmlFor="timezone">Timezone</Label>
							<Select
								value={timezone}
								onValueChange={setTimezone}
							>
								<SelectTrigger id="timezone">
									<SelectValue placeholder="Select timezone..." />
								</SelectTrigger>
								<SelectContent>
									{COMMON_TIMEZONES.map((tz) => (
										<SelectItem key={tz} value={tz}>
											{tz}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							<p className="text-xs text-muted-foreground">
								Timezone for evaluating the cron expression
							</p>
						</div>

						{/* Enabled Toggle */}
						<div className="flex items-center justify-between">
							<div className="space-y-0.5">
								<Label htmlFor="schedule-enabled">
									Enabled
								</Label>
								<p className="text-xs text-muted-foreground">
									When disabled, the schedule will not trigger
									events
								</p>
							</div>
							<Switch
								id="schedule-enabled"
								checked={scheduleEnabled}
								onCheckedChange={setScheduleEnabled}
							/>
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
					Save Changes
				</Button>
			</DialogFooter>
		</form>
	);
}

export function EditEventSourceDialog({
	source,
	open,
	onOpenChange,
}: EditEventSourceDialogProps) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[500px]">
				{open && source && (
					<EditEventSourceDialogContent
						source={source}
						onOpenChange={onOpenChange}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
