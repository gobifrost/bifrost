import { useCallback, useEffect, useRef, useState } from "react";
import { Cloud, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { SettingsToggleRow } from "@/components/shared/SettingsToggleRow";
import { observePlatformJob } from "@/services/platformJobs";
import { webSocketService, type PlatformJobUpdate } from "@/services/websocket";
import {
	connectCloudflareCapacity,
	getCloudflareCapacity,
	updateCloudflareCapacityRules,
	type CloudflareCapacityConnectRequest,
	type CloudflareCapacityRules,
	type CloudflareCapacityStatus,
} from "@/services/workerCapacity";

const TERMINAL_JOB_STATUSES = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

const DEFAULT_RULES: CloudflareCapacityRules = {
	workflows: true,
	agents: true,
	app_builds: true,
	other_jobs: true,
	prefer_kubernetes: false,
};

function initialPublicOrigin() {
	if (typeof window === "undefined" || window.location.protocol !== "https:") {
		return "";
	}
	return window.location.origin;
}

function maxWorkersValue(value: string): number | null {
	const parsed = Number(value);
	return Number.isInteger(parsed) && parsed >= 1 && parsed <= 50
		? parsed
		: null;
}

function statusPresentation(status: CloudflareCapacityStatus["status"]) {
	switch (status) {
		case "ready":
			return { label: "Provider Ready", variant: "success" as const };
		case "provisioning":
			return { label: "Setup In Progress", variant: "info" as const };
		case "account_selection_required":
			return {
				label: "Account Selection Required",
				variant: "warning" as const,
			};
		case "failed":
			return { label: "Setup Needs Attention", variant: "destructive" as const };
		default:
			return { label: "Not Connected", variant: "outline" as const };
	}
}

function formatBytes(bytes: number | null | undefined) {
	if (bytes === 4 * 1024 ** 3) return "4 GiB";
	if (bytes == null) return null;
	return `${Math.round(bytes / 1024 ** 3)} GiB`;
}

function isTerminalJob(job: PlatformJobUpdate) {
	return TERMINAL_JOB_STATUSES.has(job.status);
}

export function CloudflareCapacitySettings() {
	const mounted = useRef(true);
	const [capacity, setCapacity] = useState<CloudflareCapacityStatus | null>(null);
	const [loading, setLoading] = useState(true);
	const [loadError, setLoadError] = useState<string | null>(null);
	const [apiKey, setApiKey] = useState("");
	const [publicOrigin, setPublicOrigin] = useState(initialPublicOrigin);
	const [maxWorkers, setMaxWorkers] = useState("2");
	const [accountId, setAccountId] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const [submitError, setSubmitError] = useState<string | null>(null);
	const [jobId, setJobId] = useState<string | null>(null);
	const [progress, setProgress] = useState<string | null>(null);
	const [rules, setRules] = useState<CloudflareCapacityRules>(DEFAULT_RULES);
	const [savingRules, setSavingRules] = useState(false);
	const [rulesError, setRulesError] = useState<string | null>(null);

	const applyCapacity = useCallback((status: CloudflareCapacityStatus) => {
		setCapacity(status);
		if (status.max_workers != null) {
			setMaxWorkers(String(status.max_workers));
		}
		setAccountId(status.account_id ?? "");
		setRules(status.rules ?? DEFAULT_RULES);
		if (status.status === "provisioning" && status.last_job_id) {
			setJobId(status.last_job_id);
		}
	}, []);

	const fetchCapacity = useCallback(async () => {
		const status = await getCloudflareCapacity();
		if (!mounted.current) return;
		applyCapacity(status);
	}, [applyCapacity]);

	const loadCapacity = useCallback(async () => {
		setLoading(true);
		setLoadError(null);
		try {
			await fetchCapacity();
		} catch (error) {
			if (mounted.current) {
				setLoadError(
					error instanceof Error
						? error.message
						: "Could not load Cloudflare capacity.",
				);
			}
		} finally {
			if (mounted.current) setLoading(false);
		}
	}, [fetchCapacity]);

	useEffect(() => {
		mounted.current = true;
		const initialLoad = async () => {
			try {
				await fetchCapacity();
			} catch (error) {
				if (mounted.current) {
					setLoadError(
						error instanceof Error
							? error.message
							: "Could not load Cloudflare capacity.",
					);
				}
			} finally {
				if (mounted.current) setLoading(false);
			}
		};
		void initialLoad();
		return () => {
			mounted.current = false;
		};
	}, [fetchCapacity]);

	useEffect(() => {
		if (!jobId) return;
		let terminalHandled = false;
		const handleUpdate = (job: PlatformJobUpdate) => {
			if (!mounted.current) return;
			if (job.progress.phase) setProgress(job.progress.phase);
			if (!isTerminalJob(job) || terminalHandled) return;
			terminalHandled = true;
			setJobId(null);
			setProgress(null);
			void loadCapacity();
		};
		// Subscribe first, then recover the narrow accepted-response-to-notification
		// race with one durable snapshot. Progress remains notification-driven.
		const unsubscribe = webSocketService.onPlatformJobUpdate(jobId, handleUpdate);
		const observation = observePlatformJob(jobId, handleUpdate);
		void observation.promise.catch(() => undefined);
		return () => {
			unsubscribe();
			observation.cancel();
		};
	}, [jobId, loadCapacity]);

	const selectedAccountRequired =
		capacity?.status === "account_selection_required" &&
		(capacity.accounts?.length ?? 0) > 1;
	const workerLimit = maxWorkersValue(maxWorkers);
	const initialConnection = capacity?.status === "unconfigured";
	const publicOriginValid = !initialConnection || /^https:\/\/.+/.test(publicOrigin.trim());
	const busy = loading || submitting || capacity?.status === "provisioning";
	const canConnect =
		!busy &&
		workerLimit !== null &&
		publicOriginValid &&
		(!initialConnection || apiKey.trim().length > 0) &&
		(!selectedAccountRequired || accountId.length > 0);

	const submit = async (action: "connect" | "replace") => {
		if (!capacity || !canConnect) return;
		setSubmitting(true);
		setSubmitError(null);
		const request: CloudflareCapacityConnectRequest = {
			max_workers: workerLimit!,
		};
		if (apiKey.trim()) request.api_key = apiKey.trim();
		if (selectedAccountRequired) request.account_id = accountId;
		if (publicOrigin.trim()) request.bifrost_api_origin = publicOrigin.trim();
		try {
			const accepted = await connectCloudflareCapacity(request);
			if (!mounted.current) return;
			setCapacity(accepted.connection);
			setRules(accepted.connection.rules ?? DEFAULT_RULES);
			setJobId(accepted.job_id);
			setProgress("Cloudflare setup has started");
			toast.success(
				action === "replace"
					? "Cloudflare API key replacement started"
					: "Cloudflare setup started",
				{ description: "Progress is available in Notifications." },
			);
		} catch (error) {
			if (mounted.current) {
				setSubmitError(
					error instanceof Error
						? error.message
						: "Could not start Cloudflare setup.",
				);
			}
		} finally {
			// Credentials are write-only and never survive an attempted request.
			if (mounted.current) {
				setApiKey("");
				setSubmitting(false);
			}
		}
	};

	const saveRules = async () => {
		if (!capacity?.configured || savingRules) return;
		setSavingRules(true);
		setRulesError(null);
		try {
			const status = await updateCloudflareCapacityRules(rules);
			if (!mounted.current) return;
			setCapacity(status);
			setRules(status.rules ?? rules);
			toast.success("Cloudflare start rules saved");
		} catch (error) {
			if (mounted.current) {
				setRulesError(
					error instanceof Error
						? error.message
						: "Could not save Cloudflare start rules.",
				);
			}
		} finally {
			if (mounted.current) setSavingRules(false);
		}
	};

	const presentation = capacity ? statusPresentation(capacity.status) : null;
	const memoryLimit = formatBytes(capacity?.memory_limit_bytes);
	const actionLabel =
		capacity?.status === "account_selection_required"
			? "Continue Setup"
			: capacity?.status === "failed"
				? "Retry Setup"
				: capacity?.status === "ready"
					? "Update Workers"
					: "Connect Cloudflare";
	const rulesDirty = capacity
		? Object.keys(DEFAULT_RULES).some(
				(key) =>
					rules[key as keyof CloudflareCapacityRules] !==
					(capacity.rules ?? DEFAULT_RULES)[key as keyof CloudflareCapacityRules],
			)
		: false;
	const rulesDisabled = !capacity?.configured || savingRules || submitting;

	return (
		<Card>
			<CardHeader>
				<div className="flex flex-wrap items-center gap-2">
					<Cloud aria-hidden="true" className="size-5" />
					<CardTitle>Cloudflare Workers</CardTitle>
					{presentation && <Badge variant={presentation.variant}>{presentation.label}</Badge>}
				</div>
				<CardDescription>
					Connect Cloudflare as an additional worker provider for this Bifrost
					installation.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{loading && (
					<p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
						<Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />
						Loading Cloudflare capacity…
					</p>
				)}
				{loadError && (
					<SettingsLoadError
						name="Cloudflare capacity"
						onRetry={() => void loadCapacity()}
					/>
				)}
				{capacity && !loading && (
					<>
						{capacity.status === "ready" && (
							<p role="status" className="text-sm text-muted-foreground">
								Cloudflare has verified this provider is ready. This does not report
								running workers.
							</p>
						)}
						{capacity.status === "provisioning" && (
							<p role="status" className="text-sm text-muted-foreground">
								{progress ?? "Cloudflare setup is in progress."} Progress is available
								in Notifications.
							</p>
						)}
						{capacity.status === "account_selection_required" && (
							<p role="status" className="text-sm text-muted-foreground">
								Choose the Cloudflare account for this installation to continue setup.
							</p>
						)}
						{capacity.error && (
							<p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">
								{capacity.error}
							</p>
						)}
						{submitError && (
							<p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">
								{submitError}
							</p>
						)}

						{capacity.status !== "provisioning" && (
							<div className="grid gap-5 sm:grid-cols-2">
								<div className="space-y-2">
									<Label htmlFor="cloudflare-max-workers">Max Cloudflare Workers</Label>
									<Input
										id="cloudflare-max-workers"
										type="number"
										inputMode="numeric"
										min={1}
										max={50}
										className="min-h-11 w-full sm:w-40"
										value={maxWorkers}
										disabled={busy}
										aria-invalid={workerLimit === null || undefined}
										onChange={(event) => setMaxWorkers(event.target.value)}
									/>
									<p className="text-sm text-muted-foreground">
										Choose 1–50 workers. The default is 2.
									</p>
								</div>
								{selectedAccountRequired && (
									<div className="space-y-2">
										<Label htmlFor="cloudflare-account">Cloudflare Account</Label>
										<Select value={accountId} onValueChange={setAccountId} disabled={busy}>
											<SelectTrigger id="cloudflare-account" className="min-h-11 w-full">
												<SelectValue placeholder="Select an account" />
											</SelectTrigger>
											<SelectContent>
												{capacity.accounts?.map((account) => (
													<SelectItem key={account.id} value={account.id} className="min-h-11">
														{account.name}
													</SelectItem>
												))}
											</SelectContent>
										</Select>
									</div>
								)}
							</div>
						)}

						{initialConnection && (
							<div className="space-y-5 border-t pt-5">
								<div className="space-y-2">
									<Label htmlFor="cloudflare-api-key">Cloudflare API Key</Label>
									<Input
										id="cloudflare-api-key"
										type="password"
										autoComplete="off"
										className="min-h-11"
										value={apiKey}
										disabled={busy}
										onChange={(event) => setApiKey(event.target.value)}
									/>
									<p className="text-sm text-muted-foreground">
										This key is write-only and is cleared after each setup attempt.
									</p>
								</div>
								<div className="space-y-2">
									<Label htmlFor="cloudflare-public-origin">Bifrost Public API URL</Label>
									<Input
										id="cloudflare-public-origin"
										type="url"
										inputMode="url"
										placeholder="https://bifrost.example.com"
										className="min-h-11"
										value={publicOrigin}
										disabled={busy}
										aria-invalid={!publicOriginValid || undefined}
										onChange={(event) => setPublicOrigin(event.target.value)}
									/>
									<p className="text-sm text-muted-foreground">
										{publicOrigin
											? "Cloudflare needs a public HTTPS URL that it can reach."
											: "This installation is not using HTTPS. Enter a public HTTPS API URL before connecting."}
									</p>
								</div>
							</div>
						)}

						{capacity.status === "ready" && (
							<>
								<dl className="grid gap-4 border-t pt-5 text-sm sm:grid-cols-2">
									<div><dt className="text-muted-foreground">Memory Limit</dt><dd className="mt-1 font-medium">{memoryLimit ?? "4 GiB"} per worker (fixed)</dd></div>
									<div><dt className="text-muted-foreground">Worker Version</dt><dd className="mt-1 font-medium [overflow-wrap:anywhere]">{capacity.worker_version ?? "Unavailable"}</dd></div>
								</dl>
								<div className="space-y-2 border-t pt-5">
									<Label htmlFor="cloudflare-replace-api-key">Replace Cloudflare API Key</Label>
									<Input
										id="cloudflare-replace-api-key"
										type="password"
										autoComplete="off"
										className="min-h-11"
										value={apiKey}
										disabled={busy}
										onChange={(event) => setApiKey(event.target.value)}
									/>
									<p className="text-sm text-muted-foreground">
										Enter a new key only when replacing the saved key.
									</p>
								</div>
							</>
						)}

						<section className="space-y-4 border-t pt-5" aria-labelledby="cloudflare-start-rules">
							<div className="space-y-1">
								<h3 id="cloudflare-start-rules" className="font-medium">Cloudflare Start Rules</h3>
								<p className="text-sm text-muted-foreground">
									Choose which Normal work can start Cloudflare capacity. Available local workers are always reused first, and Fast Path stays local.
								</p>
							</div>
							<div className="space-y-2">
								<SettingsToggleRow
									id="cloudflare-workflow-rules"
									label="Run Workflows and Services"
									description="Start supported Normal workflow and service work on Cloudflare when local workers are unavailable."
									checked={rules.workflows}
									disabled={rulesDisabled}
									onChange={(workflows) => setRules((current) => ({ ...current, workflows }))}
								/>
								<SettingsToggleRow
									id="cloudflare-agent-rules"
									label="Run Agents and Summaries"
									description="Start supported Normal agents and summaries on Cloudflare when local workers are unavailable."
									checked={rules.agents}
									disabled={rulesDisabled}
									onChange={(agents) => setRules((current) => ({ ...current, agents }))}
								/>
								<SettingsToggleRow
									id="cloudflare-app-build-rules"
									label="Run App Builds"
									description="Start compatible Normal independent App deployments and SDK updates on Cloudflare. High-memory work stays on other capacity."
									checked={rules.app_builds}
									disabled={rulesDisabled}
									onChange={(app_builds) => setRules((current) => ({ ...current, app_builds }))}
								/>
								<SettingsToggleRow
									id="cloudflare-other-job-rules"
									label="Run Other Jobs"
									description="Start other supported Normal jobs on Cloudflare when local workers are unavailable."
									checked={rules.other_jobs}
									disabled={rulesDisabled}
									onChange={(other_jobs) => setRules((current) => ({ ...current, other_jobs }))}
								/>
								<SettingsToggleRow
									id="cloudflare-prefer-kubernetes"
									label="Let Kubernetes Scale First"
									description="Let Kubernetes add worker capacity before Cloudflare starts selected Normal work."
									checked={rules.prefer_kubernetes}
									disabled={rulesDisabled}
									onChange={(prefer_kubernetes) => setRules((current) => ({ ...current, prefer_kubernetes }))}
								/>
							</div>
							<p className="text-sm text-muted-foreground">
								With no local worker or Kubernetes capacity, Cloudflare handles all supported Normal work. A running Cloudflare worker can finish any supported Normal work.
							</p>
							{rulesError && <p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">{rulesError}</p>}
							<Button type="button" variant="outline" className="min-h-11" disabled={rulesDisabled || !rulesDirty} onClick={() => void saveRules()}>
								{savingRules && <Loader2 aria-hidden="true" className="mr-2 size-4 animate-spin motion-reduce:animate-none" />}
								Save Cloudflare Rules
							</Button>
						</section>

						{capacity.status !== "provisioning" && (
							<div className="flex flex-wrap items-center gap-3 border-t pt-5">
								<Button type="button" className="min-h-11" disabled={!canConnect} onClick={() => void submit("connect")}>
									{submitting && <Loader2 aria-hidden="true" className="mr-2 size-4 animate-spin motion-reduce:animate-none" />}
									{actionLabel}
								</Button>
								{capacity.status === "ready" && (
									<Button type="button" variant="outline" className="min-h-11" disabled={busy || !apiKey.trim() || workerLimit === null} onClick={() => void submit("replace")}>
										Replace API Key
									</Button>
								)}
								<Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => void loadCapacity()}>
									<RefreshCw aria-hidden="true" className="mr-2 size-4" />
									Reload Status
								</Button>
							</div>
						)}
					</>
				)}
			</CardContent>
		</Card>
	);
}
