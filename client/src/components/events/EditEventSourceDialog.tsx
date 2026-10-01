import { useState, useEffect, useId, useMemo, useRef } from "react";
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

import type { components } from "@/lib/v1";

type CronValidationResult = components["schemas"]["CronValidationResponse"];

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
	updateMutation,
}: {
	source: EventSource;
	updateMutation: ReturnType<typeof useUpdateEventSource>;
	onOpenChange: (open: boolean) => void;
}) {
	const { isPlatformAdmin } = useAuth();
	const formId = useId();

	// Fetch adapter metadata for dynamic config
	const {
		data: adaptersData,
		isError: adaptersError,
		isFetching: adaptersFetching,
		refetch: refetchAdapters,
	} = useWebhookAdapters();
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
	const [webhookConfig, setWebhookConfig] = useState<Record<string, unknown>>(
		source.webhook?.config || {},
	);

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
	const [cronValidation, setCronValidation] = useState<{
		expression: string;
		timezone: string;
		result: CronValidationResult;
	} | null>(null);

	const [errors, setErrors] = useState<string[]>([]);
	const errorSummaryRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (errors.length) errorSummaryRef.current?.focus();
	}, [errors]);

	const isLoading = updateMutation.isPending;
	const isWebhook = source.source_type === "webhook";
	const isSchedule = source.source_type === "schedule";

	// Ignore and cancel validation for a previous expression or timezone.
	useEffect(() => {
		const expression = cronExpression.trim();
		if (!isSchedule || !expression) return;
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
	}, [cronExpression, timezone, isSchedule]);

	const displayCronValidation =
		cronValidation?.expression === cronExpression.trim() &&
		cronValidation.timezone === timezone
			? cronValidation.result
			: null;

	const validateForm = (): boolean => {
		const newErrors: string[] = [];

		if (!name.trim()) {
			newErrors.push("Name is required");
		}

		if (isSchedule) {
			if (!cronExpression.trim()) {
				newErrors.push("Cron expression is required");
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
				body: body as NonNullable<
					typeof updateMutation.variables
				>["body"],
			});

			toast.success("Event source updated");
			onOpenChange(false);
		} catch (error) {
			console.error("Failed to update event source:", error);
			setErrors([
				"Could not save your changes. Your edits are still here. Try again.",
			]);
		}
	};

	return (
		<form
			onSubmit={handleSubmit}
			className="flex max-h-[calc(90dvh-3rem)] min-h-0 min-w-0 flex-col"
		>
			<DialogHeader className="shrink-0 border-b pb-4">
				<DialogTitle>Edit Event Source</DialogTitle>
				<DialogDescription>
					Update the event source settings.
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
								showGlobal
							/>
							<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
								Leave as Global to make this source available to
								all organizations.
							</p>
						</div>
					)}

					{/* Name */}
					<div className="min-w-0 space-y-2">
						<Label htmlFor={`${formId}-name`}>Name</Label>
						<Input
							className="min-h-11"
							id={`${formId}-name`}
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder="e.g., GitHub Webhooks"
						/>
					</div>

					{/* Topic (read-only) */}
					{source.source_type === "topic" && source.event_type && (
						<div className="min-w-0 space-y-2">
							<Label htmlFor={`${formId}-topic`}>Topic</Label>
							<Input
								className="min-h-11 font-mono"
								id={`${formId}-topic`}
								value={source.event_type}
								disabled
							/>
							<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
								The topic this source publishes to. Cannot be
								changed after creation.
							</p>
						</div>
					)}

					{isWebhook && adaptersError && (
						<Alert>
							<AlertCircle className="size-4" />
							<AlertDescription className="space-y-3">
								<p>
									Could not load webhook configuration.
									Existing values have been retained.
								</p>
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									disabled={adaptersFetching || isLoading}
									onClick={() => void refetchAdapters()}
								>
									Retry configuration
								</Button>
							</AlertDescription>
						</Alert>
					)}
					{/* Webhook Config (Dynamic from adapter schema) */}
					{isWebhook && hasDynamicConfig && selectedAdapter && (
						<>
							<div className="border-t pt-4">
								<h4 className="text-sm font-medium mb-3">
									Webhook Configuration
								</h4>
								{selectedAdapter.display_name !==
									"Generic Webhook" && (
									<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere] mb-3">
										Adapter: {selectedAdapter.display_name}
									</p>
								)}
							</div>
							<DynamicConfigForm
								adapterName={selectedAdapter.name}
								integrationId={
									source.webhook?.integration_id ?? undefined
								}
								requiresIntegration={
									!!selectedAdapter.requires_integration
								}
								organizationId={organizationId}
								configSchema={
									selectedAdapter.config_schema as unknown as ConfigSchema
								}
								config={webhookConfig}
								onChange={setWebhookConfig}
								secretConfigured={source.webhook?.secret_set}
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

							{source.webhook &&
								source.webhook.rate_limited_count_24h > 0 && (
									<p className="text-sm text-destructive">
										{source.webhook.rate_limited_count_24h}{" "}
										request
										{source.webhook
											.rate_limited_count_24h === 1
											? ""
											: "s"}{" "}
										rate limited in the last 24 hours.
									</p>
								)}

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
								<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
									Maximum events accepted within the window
									below. Leave empty to disable the limit.
								</p>
							</div>

							<div className="min-w-0 space-y-2">
								<Label htmlFor={`${formId}-rate-limit-window`}>
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
								<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
									Window duration. Default 60 means the limit
									above applies per minute.
								</p>
							</div>

							<div className="flex min-w-0 items-center justify-between gap-4">
								<div className="min-w-0 space-y-1">
									<Label
										className="min-h-11"
										htmlFor={`${formId}-rate-limit-enabled`}
									>
										Enabled
									</Label>
									<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
										Disable to bypass rate limiting for this
										source.
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
									placeholder="e.g., 0 9 * * * (daily at 9 AM)"
								/>
								<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
									Standard 5-field cron expression (minute
									hour day-of-month month day-of-week)
								</p>

								{cronExpression.trim() &&
									!displayCronValidation && (
										<p
											role="status"
											className="text-sm text-muted-foreground"
										>
											Checking schedule…
										</p>
									)}
								{/* Cron Validation Display */}
								{displayCronValidation && (
									<div className="mt-2">
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
											<Alert
												ref={errorSummaryRef}
												tabIndex={-1}
												className="focus:outline-none"
												variant="destructive"
											>
												<AlertCircle className="h-4 w-4" />
												<AlertDescription>
													{displayCronValidation.error ||
														displayCronValidation.human_readable}
												</AlertDescription>
											</Alert>
										)}

										{displayCronValidation.warning && (
											<Alert className="mt-2 bg-[var(--bf-warning-soft)] border-transparent">
												<AlertCircle className="h-4 w-4 text-[var(--bf-warning)]" />
												<AlertDescription className="text-[var(--bf-warning)]">
													{
														displayCronValidation.warning
													}
												</AlertDescription>
											</Alert>
										)}
									</div>
								)}
							</div>

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
										className="min-h-11 w-full"
										id={`${formId}-timezone`}
									>
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
								<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
									Timezone for evaluating the cron expression
								</p>
							</div>

							{/* Enabled Toggle */}
							<div className="flex min-w-0 items-center justify-between gap-4">
								<div className="min-w-0 space-y-1">
									<Label
										className="min-h-11"
										htmlFor={`${formId}-schedule-enabled`}
									>
										Enabled
									</Label>
									<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
										When disabled, the schedule will not
										trigger events
									</p>
								</div>
								<Switch
									disabled={isLoading}
									className="shrink-0"
									id={`${formId}-schedule-enabled`}
									checked={scheduleEnabled}
									onCheckedChange={setScheduleEnabled}
								/>
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
										className="min-h-11 w-full"
										id={`${formId}-overlap-policy`}
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
								<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
									Skip (default) drops the new run if a
									previous run is still active. Queue and
									replace are reserved for future use.
								</p>
							</div>
						</>
					)}
				</fieldset>
			</div>

			<DialogFooter className="shrink-0 border-t pt-4">
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					disabled={isLoading}
					onClick={() => onOpenChange(false)}
				>
					Cancel
				</Button>
				<Button type="submit" className="min-h-11" disabled={isLoading}>
					{isLoading && (
						<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
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
	const updateMutation = useUpdateEventSource();
	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!updateMutation.isPending) onOpenChange(nextOpen);
			}}
		>
			<DialogContent className="overflow-hidden sm:max-w-[500px]">
				{open && source && (
					<EditEventSourceDialogContent
						key={source.id}
						updateMutation={updateMutation}
						source={source}
						onOpenChange={onOpenChange}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
