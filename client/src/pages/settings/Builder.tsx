import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Activity,
	AlertTriangle,
	BarChart3,
	Check,
	CheckCircle2,
	Cloud,
	Container,
	ExternalLink,
	KeyRound,
	Laptop,
	Link2,
	Loader2,
	Play,
	Save,
	ShieldCheck,
	TimerReset,
} from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/contexts/AuthContext";
import type { components } from "@/lib/v1";
import {
	getBuilderRunnerSetup,
	provisionBuilderRunner,
	saveBuilderRunnerSetup,
	type BuilderRunnerConfigSave,
	type BuilderRunnerSetup,
} from "@/services/builderRunner";
import { getPlatformJob } from "@/services/platformJobs";
import {
	webSocketService,
	type PlatformJobUpdate,
} from "@/services/websocket";
import { cn } from "@/lib/utils";

const setupKey = ["admin", "builder", "runner"] as const;
type Provider = "cloudflare" | "local";

interface SetupDraft {
	provider: Provider;
	callbackBaseUrl: string;
	accountId: string;
	apiToken: string;
	endpointUrl: string;
	runnerSecret: string;
	enabled: boolean;
}

function draftFromSetup(setup: BuilderRunnerSetup): SetupDraft {
	const { config, recommended_callback_base_url: recommended } = setup;
	return {
		provider: config?.provider ?? "cloudflare",
		callbackBaseUrl: config?.callback_base_url ?? recommended,
		accountId: config?.cloudflare?.account_id ?? "",
		apiToken: "",
		endpointUrl: config?.local?.endpoint_url ?? "",
		runnerSecret: "",
		enabled: config?.enabled ?? false,
	};
}

function terminal(status: PlatformJobUpdate["status"]): boolean {
	return status === "succeeded" || status === "failed" || status === "cancelled";
}

export function BuilderSettings() {
	const setupQuery = useQuery({
		queryKey: setupKey,
		queryFn: ({ signal }) => getBuilderRunnerSetup(signal),
	});

	if (setupQuery.isLoading) {
		return <Skeleton className="h-[560px] w-full rounded-3xl" />;
	}
	if (setupQuery.isError || !setupQuery.data) {
		return (
			<Alert variant="destructive">
				<AlertTitle>Builder setup could not be loaded</AlertTitle>
				<AlertDescription className="mt-3">
					<Button variant="outline" size="sm" onClick={() => setupQuery.refetch()}>
						Try again
					</Button>
				</AlertDescription>
			</Alert>
		);
	}

	return (
		<BuilderSettingsContent
				key={JSON.stringify([
					setupQuery.data.config,
					setupQuery.data.active_provisioning_job_id,
				])}
				setup={setupQuery.data}
		/>
	);
}

function BuilderSettingsContent({ setup }: { setup: BuilderRunnerSetup }) {
	const queryClient = useQueryClient();
	const { user } = useAuth();
	const [draft, setDraft] = useState<SetupDraft>(() => draftFromSetup(setup));
	const [jobId, setJobId] = useState<string | null>(
		setup.active_provisioning_job_id ?? null,
	);
	const [liveJob, setLiveJob] = useState<PlatformJobUpdate | null>(null);
	const platformJobQuery = useQuery({
		queryKey: ["platform-job", jobId],
		queryFn: ({ signal }) => getPlatformJob(jobId!, signal),
		enabled: Boolean(jobId),
		retry: false,
	});
	const provisioningJob =
		liveJob?.id === jobId ? liveJob : (platformJobQuery.data ?? null);
	const readinessBlockers = setup.readiness?.blockers ?? [];

	useEffect(() => {
		if (!jobId) return;
		if (user?.id) {
			void webSocketService.connect([`notification:${user.id}`]);
		}
		return webSocketService.onPlatformJobUpdate(jobId, (job) => {
			setLiveJob(job);
			if (!terminal(job.status)) return;
			setJobId(null);
			void queryClient.invalidateQueries({ queryKey: setupKey });
			if (job.status === "succeeded") {
				toast.success("Builder runner connected");
			} else if (job.status === "failed") {
				toast.error(job.error?.message ?? "Builder runner setup failed");
			}
		});
	}, [jobId, queryClient, user?.id]);

	useEffect(() => {
		if (!platformJobQuery.data || !terminal(platformJobQuery.data.status)) return;
		void queryClient.invalidateQueries({ queryKey: setupKey });
	}, [platformJobQuery.data, queryClient]);

	const saveMutation = useMutation({
		mutationFn: (enabled: boolean) => {
			const payload: BuilderRunnerConfigSave = {
				provider: draft.provider,
				enabled,
				...(draft.provider === "local"
					? { callback_base_url: draft.callbackBaseUrl.trim() || null }
					: {}),
				cloudflare:
					draft.provider === "cloudflare"
						? {
								account_id: draft.accountId.trim() || null,
								api_token: draft.apiToken.trim() || null,
							}
						: null,
				local:
					draft.provider === "local"
						? {
								endpoint_url: draft.endpointUrl.trim() || null,
								runner_secret: draft.runnerSecret.trim() || null,
							}
						: null,
			};
			return saveBuilderRunnerSetup(payload);
		},
		onSuccess: async (config) => {
			setDraft((current) => ({
				...current,
				enabled: config.enabled,
				apiToken: "",
				runnerSecret: "",
			}));
			await queryClient.invalidateQueries({ queryKey: setupKey });
			toast.success(config.enabled ? "Builder enabled" : "Runner settings saved");
		},
		onError: (error: Error) => {
			setDraft((current) => ({
				...current,
				enabled: setup.config?.enabled ?? false,
			}));
			toast.error(error.message);
		},
	});

	const provisionMutation = useMutation({
		mutationFn: provisionBuilderRunner,
		onSuccess: (job) => {
			setJobId(job.job_id);
			setLiveJob(null);
		},
		onError: (error: Error) => toast.error(error.message),
	});

	const readiness = setup.readiness;
	const config = setup.config;
	const provisioning = Boolean(
		jobId && (!provisioningJob || !terminal(provisioningJob.status)),
	);
	const canProvision = Boolean(
		config &&
		readiness?.credentials_configured &&
		readiness.callback_configured &&
		!provisionMutation.isPending &&
		!provisioning,
	);
	const canEnable = Boolean(readiness?.provisioned && readiness.connected);
	const setupPercent = useMemo(() => {
		if (provisioningJob?.progress.percent != null) {
			return provisioningJob.progress.percent;
		}
		const checks = [
			readiness?.ai_configured,
			readiness?.credentials_configured,
			readiness?.callback_configured,
			readiness?.provisioned,
			readiness?.connected,
			readiness?.enabled,
		];
		return (checks.filter(Boolean).length / checks.length) * 100;
	}, [provisioningJob?.progress.percent, readiness]);

	return (
		<div className="space-y-6 pb-8">
			<section className="overflow-hidden rounded-3xl border bg-card">
				<div className="grid gap-6 p-5 sm:p-6 lg:grid-cols-[1fr_260px]">
					<div>
						<div className="flex flex-wrap items-center gap-2">
							<Badge
								variant={readiness?.ready ? "secondary" : "outline"}
								className={cn(
									"gap-1.5",
									readiness?.ready && "text-emerald-700 dark:text-emerald-300",
								)}
							>
								{readiness?.ready ? (
									<CheckCircle2 className="h-3.5 w-3.5" />
								) : (
									<Activity className="h-3.5 w-3.5" />
								)}
								{readiness?.ready ? "Ready for users" : "Setup in progress"}
							</Badge>
							{readiness?.provider ? (
								<Badge variant="outline">{readiness.provider}</Badge>
							) : null}
						</div>
						<h2 className="mt-4 text-2xl font-semibold tracking-tight">
							Native app building
						</h2>
						<p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
							Bifrost coordinates durable jobs in the existing scheduler. Cloudflare
							creates an isolated container only while a build is running; no permanent
							Builder container, extra public port, or per-app DNS record is required.
						</p>
					</div>
					<div className="rounded-2xl bg-muted/40 p-4">
						<div className="flex items-center justify-between text-xs">
							<span className="font-medium">Readiness</span>
							<span className="text-muted-foreground">{Math.round(setupPercent)}%</span>
						</div>
						<Progress value={setupPercent} className="mt-2 h-1.5" />
						<p className="mt-3 text-xs leading-5 text-muted-foreground">
							{provisioningJob?.progress.phase ??
								(readiness?.ready
									? "AI, runner, and user access are connected."
									: "Complete the checks below, then enable Builder.")}
						</p>
					</div>
				</div>
			</section>

			<ReadinessChecklist readiness={readiness} />
			{!readiness?.ready && readinessBlockers.length > 0 ? (
				<Alert>
					<AlertTriangle className="h-4 w-4" />
					<AlertTitle>Finish these setup requirements</AlertTitle>
					<AlertDescription>
						<ul className="mt-2 space-y-2">
							{readinessBlockers.map((blocker) => (
								<li key={blocker.code}>
									<span className="font-medium text-foreground">
										{blocker.message}
									</span>{" "}
									{blocker.action}
								</li>
							))}
						</ul>
					</AlertDescription>
				</Alert>
			) : null}

			<section className="space-y-4 rounded-3xl border bg-card p-5 sm:p-6">
				<div>
					<h3 className="text-lg font-semibold">1. Choose where builds run</h3>
					<p className="mt-1 text-sm text-muted-foreground">
						Both options use the same provider-neutral job and callback contract.
					</p>
				</div>
				<div className="grid gap-3 sm:grid-cols-2">
					<ProviderChoice
						active={draft.provider === "cloudflare"}
						icon={Cloud}
						title="Cloudflare"
						detail="Recommended for production. Containers scale to zero between jobs."
						onClick={() => setDraft((current) => ({ ...current, provider: "cloudflare" }))}
					/>
					<ProviderChoice
						active={draft.provider === "local"}
						icon={Laptop}
						title="Self-hosted runner"
						detail="For development or infrastructure you operate on your private network."
						onClick={() => setDraft((current) => ({ ...current, provider: "local" }))}
					/>
				</div>

				{draft.provider === "cloudflare" ? (
					<div className="grid gap-4 sm:grid-cols-2">
						<Field
							id="cloudflare-account"
							label="Account ID"
							value={draft.accountId}
							onChange={(accountId) => setDraft((current) => ({ ...current, accountId }))}
							placeholder="Cloudflare account ID"
						/>
						<Field
							id="cloudflare-token"
							label="API token"
							value={draft.apiToken}
							onChange={(apiToken) => setDraft((current) => ({ ...current, apiToken }))}
							placeholder={config?.cloudflare?.api_token_set ? "Saved — enter only to replace" : "Cloudflare API token"}
							type="password"
						/>
						<p className="sm:col-span-2 flex items-start gap-2 rounded-2xl bg-muted/35 p-3 text-xs leading-5 text-muted-foreground">
							<KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
							Create a scoped token with {setup.cloudflare_permissions?.join(", ") || "Workers Scripts Write"}. Bifrost encrypts it and never returns it to the browser.
						</p>
						<div className="sm:col-span-2 flex items-start gap-3 rounded-2xl border bg-muted/20 p-3">
							<Link2 className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
							<div className="min-w-0">
								<p className="text-xs font-medium">Bifrost address</p>
								<p className="mt-1 truncate font-mono text-xs text-muted-foreground">
									{setup.recommended_callback_base_url}
								</p>
								<p className="mt-1 text-xs leading-5 text-muted-foreground">
									Bifrost uses its existing address automatically. No additional hostname, DNS record, or forwarded port is needed.
								</p>
							</div>
						</div>
					</div>
				) : (
					<div className="grid gap-4 sm:grid-cols-2">
						<Field
							id="runner-endpoint"
							label="Runner endpoint"
							value={draft.endpointUrl}
							onChange={(endpointUrl) => setDraft((current) => ({ ...current, endpointUrl }))}
							placeholder="http://runner:8787"
						/>
						<Field
							id="runner-secret"
							label="Shared secret"
							value={draft.runnerSecret}
							onChange={(runnerSecret) => setDraft((current) => ({ ...current, runnerSecret }))}
							placeholder={config?.local?.runner_secret_set ? "Saved — enter only to replace" : "Generated when left blank"}
							type="password"
						/>
						<div className="space-y-2 sm:col-span-2">
							<Label htmlFor="callback-url">Bifrost callback address</Label>
							<div className="relative">
								<Link2 className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
								<Input
									id="callback-url"
									className="pl-9"
									value={draft.callbackBaseUrl}
									onChange={(event) => setDraft((current) => ({ ...current, callbackBaseUrl: event.target.value }))}
									placeholder={setup.recommended_callback_base_url}
								/>
							</div>
							<p className="text-xs leading-5 text-muted-foreground">
								Use an address the self-hosted runner can reach. The current Bifrost address is filled in by default.
							</p>
						</div>
					</div>
				)}

				<div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
					<div className="flex items-center gap-2 text-xs text-muted-foreground">
						<Container className="h-3.5 w-3.5" />
						<span className="truncate font-mono">{setup.runner_image}</span>
					</div>
					<Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate(draft.enabled)}>
						{saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
						Save settings
					</Button>
				</div>
			</section>

			<section className="grid gap-4 rounded-3xl border bg-card p-5 sm:p-6 lg:grid-cols-[1fr_auto] lg:items-center">
				<div>
					<h3 className="text-lg font-semibold">2. Deploy and verify the runner</h3>
					<p className="mt-1 text-sm text-muted-foreground">
						Provisioning deploys the control script, starts one real container, runs its self-test, and reports progress here live.
					</p>
						{provisioningJob ? (
							<div className="mt-4 max-w-xl" role="status" aria-live="polite">
								<div className="flex items-center justify-between text-xs">
									<span>{provisioningJob.progress.phase ?? "Provisioning Builder runner"}</span>
									<span>{Math.round(provisioningJob.progress.percent ?? 0)}%</span>
								</div>
								<Progress value={provisioningJob.progress.percent ?? 0} className="mt-2 h-1.5" />
						</div>
					) : null}
				</div>
				<Button variant="outline" disabled={!canProvision} onClick={() => provisionMutation.mutate()}>
					{provisionMutation.isPending || provisioning ? (
						<Loader2 className="h-4 w-4 animate-spin" />
					) : readiness?.connected ? (
						<Check className="h-4 w-4 text-emerald-500" />
					) : (
						<Play className="h-4 w-4" />
					)}
					{readiness?.connected ? "Test again" : "Provision and test"}
				</Button>
			</section>

			<section className="flex flex-col gap-4 rounded-3xl border bg-card p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
				<div>
					<h3 className="text-lg font-semibold">3. Enable Builder for users</h3>
					<p className="mt-1 text-sm text-muted-foreground">
						Build stays hidden from ordinary users until AI and the runner are connected and this switch is on.
					</p>
				</div>
				<div className="flex items-center gap-3">
					<span className="text-sm text-muted-foreground">{draft.enabled ? "Enabled" : "Disabled"}</span>
					<Switch
						aria-label="Enable Builder for users"
						checked={draft.enabled}
						disabled={!canEnable || saveMutation.isPending}
						onCheckedChange={(enabled) => {
							setDraft((current) => ({ ...current, enabled }));
							saveMutation.mutate(enabled);
						}}
					/>
				</div>
			</section>

			<section className="space-y-4 rounded-3xl border bg-card p-5 sm:p-6">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div>
						<h3 className="text-lg font-semibold">Usage, limits, and billing</h3>
						<p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
							Builders see their enforced turn budget while they work. Administrators can trace AI spend to the user and customer organization that created it.
						</p>
					</div>
					<Button asChild variant="outline" size="sm">
						<Link to="/reports/usage">
							<BarChart3 className="h-4 w-4" /> View AI usage
						</Link>
					</Button>
				</div>
				<div className="grid gap-px overflow-hidden rounded-2xl border bg-border md:grid-cols-3">
					<UsageFact
						icon={ShieldCheck}
						title="Hard turn limits"
						detail="The Builder agent's call and token limits stop a turn before it can exceed its configured budget."
					/>
					<UsageFact
						icon={BarChart3}
						title="Attributed AI spend"
						detail="Provider, model, tokens, and estimated model cost are recorded by user and organization in Bifrost."
					/>
					<UsageFact
						icon={TimerReset}
						title="Runner consumption"
						detail="Bifrost records the external run and duration. Cloudflare container charges remain in the connected Cloudflare account."
					/>
				</div>
			</section>
		</div>
	);
}

function UsageFact({
	icon: Icon,
	title,
	detail,
}: {
	icon: typeof ShieldCheck;
	title: string;
	detail: string;
}) {
	return (
		<div className="flex items-start gap-3 bg-card p-4">
			<span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">
				<Icon className="h-4 w-4" />
			</span>
			<div>
				<p className="text-sm font-medium">{title}</p>
				<p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
			</div>
		</div>
	);
}

function ReadinessChecklist({
	readiness,
}: {
	readiness: components["schemas"]["SandboxRunnerReadiness"] | undefined;
}) {
	const checks = [
		{
			label: "AI provider and Builder model",
			ready: readiness?.ai_configured,
			action: (
				<Button asChild variant="ghost" size="sm">
					<Link to="/settings/ai">
						Configure AI <ExternalLink className="h-3.5 w-3.5" />
					</Link>
				</Button>
			),
		},
		{ label: "Runner credentials", ready: readiness?.credentials_configured },
		{ label: "Bifrost address", ready: readiness?.callback_configured },
		{ label: "Runner provisioned", ready: readiness?.provisioned },
		{ label: "Live connection verified", ready: readiness?.connected },
	] as const;
	return (
		<section className="grid gap-px overflow-hidden rounded-3xl border bg-border sm:grid-cols-5">
			{checks.map((check) => (
				<div key={check.label} className="flex min-h-20 items-center gap-3 bg-card p-4">
					{check.ready ? (
						<CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-500" />
					) : (
						<AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />
					)}
					<div className="min-w-0">
						<p className="text-xs font-medium leading-4">{check.label}</p>
						{"action" in check ? check.action : null}
					</div>
				</div>
			))}
		</section>
	);
}

function ProviderChoice({
	active,
	icon: Icon,
	title,
	detail,
	onClick,
}: {
	active: boolean;
	icon: typeof Cloud;
	title: string;
	detail: string;
	onClick: () => void;
}) {
	return (
		<button
			type="button"
			aria-pressed={active}
			className={cn(
				"flex min-h-24 items-start gap-3 rounded-2xl border p-4 text-left transition-colors",
				active ? "border-primary bg-primary/5 ring-1 ring-primary/20" : "hover:bg-muted/40",
			)}
			onClick={onClick}
		>
			<span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl", active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
				<Icon className="h-4 w-4" />
			</span>
			<span>
				<span className="flex items-center gap-2 text-sm font-medium">
					{title} {active ? <ShieldCheck className="h-3.5 w-3.5 text-primary" /> : null}
				</span>
				<span className="mt-1 block text-xs leading-5 text-muted-foreground">{detail}</span>
			</span>
		</button>
	);
}

function Field({
	id,
	label,
	value,
	onChange,
	placeholder,
	type = "text",
}: {
	id: string;
	label: string;
	value: string;
	onChange: (value: string) => void;
	placeholder: string;
	type?: "text" | "password";
}) {
	return (
		<div className="space-y-2">
			<Label htmlFor={id}>{label}</Label>
			<Input id={id} type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
		</div>
	);
}
