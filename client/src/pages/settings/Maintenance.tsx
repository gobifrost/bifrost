import { DocsIndexResults, AppDepScanResults, type DocsIndexResponse, type AppDependencyScanResponse } from "./maintenance/MaintenanceResults";
import { MaintenanceActionRow, type MaintenanceOutcome } from "./maintenance/MaintenanceActionRow";
/**
 * Workspace Maintenance Settings
 *
 * Platform admin page for managing workspace maintenance operations.
 * Provides tools for documentation indexing and app dependency scanning.
 */

import { useState, useEffect, useRef } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
	Loader2,
	Settings2,
	Database,
	AppWindow,
	Download,
	Upload,
	Play,
	RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";
import { exportAll } from "@/services/exportImport";
import { ImportDialog } from "@/components/ImportDialog";
import { ArtifactRetentionSettings } from "@/pages/settings/ArtifactRetentionSettings";
import { AuditRetentionSettings } from "@/pages/settings/AuditRetentionSettings";

type ScanResultType = "none" | "docs" | "app-deps";

export function Maintenance() {
	const reimportController = useRef<AbortController | null>(null);
	useEffect(() => () => reimportController.current?.abort(), []);
	// Checklist state
	const [selectedActions, setSelectedActions] = useState<Set<string>>(new Set());
	const [runningAction, setRunningAction] = useState<string | null>(null);
	const [actionOutcomes, setActionOutcomes] = useState<Record<string, MaintenanceOutcome>>({});
	const [actionQueue, setActionQueue] = useState<string[]>([]);

	// Results
	const [lastScanType, setLastScanType] = useState<ScanResultType>("none");
	const [docsIndexResult, setDocsIndexResult] =
		useState<DocsIndexResponse | null>(null);
	const [appDepScanResult, setAppDepScanResult] =
		useState<AppDependencyScanResponse | null>(null);

	const [isExportingAll, setIsExportingAll] = useState(false);
	const [isImportAllOpen, setIsImportAllOpen] = useState(false);

	const isAnyRunning = runningAction !== null || actionQueue.length > 0;

	const finishAction = (actionId: string, outcome: MaintenanceOutcome) => {
		setActionOutcomes((previous) => ({ ...previous, [actionId]: outcome }));
		setRunningAction(null);
	};

	const handleDocsIndex = async () => {
		setRunningAction("docs");
		let outcome: MaintenanceOutcome = "failed";

		try {
			const response = await authFetch("/api/maintenance/index-docs", {
				method: "POST",
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("Documentation indexing failed", {
					description: errorData.detail || "Unknown error",
				});
				return;
			}

			const data: DocsIndexResponse = await response.json();
			outcome = data.status === "complete" ? "complete" : data.status === "skipped" ? "skipped" : "failed";
			setDocsIndexResult(data);
			setLastScanType("docs");

			if (data.status === "complete") {
				toast.success("Documentation indexed successfully", {
					description: data.message,
				});
			} else if (data.status === "skipped") {
				toast.info("Documentation indexing skipped", {
					description: data.message,
				});
			} else {
				toast.error("Documentation indexing failed", {
					description: data.message || "Unknown error",
				});
			}
		} catch (err) {
			toast.error("Documentation indexing failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			finishAction("docs", outcome);
		}
	};

	const handleAppDepScan = async () => {
		setRunningAction("app-deps");
		let outcome: MaintenanceOutcome = "failed";

		try {
			const response = await authFetch("/api/maintenance/scan-app-dependencies", {
				method: "POST",
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("App dependency scan failed", {
					description: errorData.detail || "Unknown error",
				});
				return;
			}

			const data: AppDependencyScanResponse = await response.json();
			outcome = "complete";
			setAppDepScanResult(data);
			setLastScanType("app-deps");

			if (data.issues_found === 0) {
				toast.success("Dependencies rebuilt successfully", {
					description: `Scanned ${data.apps_scanned} apps, ${data.files_scanned} files, rebuilt ${data.dependencies_rebuilt} dependencies`,
				});
			} else {
				toast.warning("Dependencies rebuilt with issues", {
					description: `Rebuilt ${data.dependencies_rebuilt} dependencies, found ${data.issues_found} broken references`,
				});
			}
		} catch (err) {
			toast.error("App dependency scan failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			finishAction("app-deps", outcome);
		}
	};

	const handleReimport = async () => {
		const controller = new AbortController();
		reimportController.current = controller;
		const { signal } = controller;
		setRunningAction("reimport");
		let outcome: MaintenanceOutcome = "failed";

		try {
			const response = await authFetch("/api/maintenance/reimport", {
				method: "POST",
				signal,
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				if (signal.aborted) return;
				toast.error("Reimport failed", {
					description: errorData.detail || "Unknown error",
				});
				return;
			}

			const { job_id } = await response.json();
			if (signal.aborted) return;
			toast.info("Reimport started", {
				description: "Re-importing entities from repository...",
			});

			// Poll for job completion
			const poll = async () => {
				for (let i = 0; i < 120; i++) {
					if (signal.aborted) return;
					await new Promise<void>((resolve) => {
						const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
						const timer = setTimeout(done, 2000);
						signal.addEventListener("abort", done, { once: true });
					});
					if (signal.aborted) return;
					try {
						const res = await authFetch(`/api/jobs/${job_id}`, { signal });
						if (!res.ok) continue;
						const result = await res.json();
						if (signal.aborted) return;
						if (result.status === "success") {
							outcome = "complete";
							toast.success("Reimport complete", {
								description: result.message || "All entities reimported",
							});
							return;
						} else if (result.status === "failed") {
							toast.error("Reimport failed", {
								description: result.error || "Unknown error",
							});
							return;
						}
						// status === "pending" — keep polling
					} catch {
						if (signal.aborted) return;
						// Network error, keep polling
					}
				}
				outcome = "unknown";
				toast.warning("Reimport timed out", {
					description: "Job may still be running. Check scheduler logs.",
				});
			};

			await poll();
		} catch (err) {
			if (signal.aborted) return;
			toast.error("Reimport failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			if (!signal.aborted) finishAction("reimport", outcome);
			if (reimportController.current === controller) reimportController.current = null;
		}
	};

	const handleExportAll = async () => {
		setIsExportingAll(true);
		try {
			await exportAll({});
			toast.success("Export downloaded");
		} catch {
			toast.error("Export failed");
		} finally {
			setIsExportingAll(false);
		}
	};

	const toggleAction = (id: string) => {
		setSelectedActions((prev) => {
			const next = new Set(prev);
			if (next.has(id)) {
				next.delete(id);
			} else {
				next.add(id);
			}
			return next;
		});
	};

	// Process the queue - runs next action when current one finishes. The
	// dequeue+dispatch is wrapped in a microtask so it does not run inside
	// the synchronous body of the effect (which would synchronously call
	// setActionQueue and trigger the set-state-in-effect rule).
	useEffect(() => {
		if (runningAction !== null || actionQueue.length === 0) return;

		let cancelled = false;
		queueMicrotask(() => {
			if (cancelled) return;
			const [next, ...rest] = actionQueue;
			setActionQueue(rest);

			const handlers: Record<string, () => Promise<void>> = {
				docs: handleDocsIndex,
				"app-deps": handleAppDepScan,
				reimport: handleReimport,
			};

			void handlers[next]?.();
		});
		return () => { cancelled = true; };
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [runningAction, actionQueue]);

	const handleRunSelected = () => {
		if (selectedActions.size === 0 || isAnyRunning) return;
		setActionOutcomes({});
		const order = ["docs", "app-deps", "reimport"];
		const queue = order.filter((id) => selectedActions.has(id));
		setActionQueue(queue);
	};

	const actions = [
		{
			id: "docs",
			icon: Database,
			label: "Index Documents",
			description: "Index platform docs into knowledge store",
		},
		{
			id: "app-deps",
			icon: AppWindow,
			label: "Rebuild App Dependencies",
			description: "Rebuild app dependency graph",
		},
		{
			id: "reimport",
			icon: RefreshCw,
			label: "Reimport from Repository",
			description:
				"Re-read workspace from S3 and reimport all entities (workflows, forms, agents, apps)",
		},
	];

	return (
		<div className="space-y-6">
			<ArtifactRetentionSettings />
			<AuditRetentionSettings />

			{/* Export/Import Card */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Download className="h-5 w-5" />
						Export / Import
					</CardTitle>
					<CardDescription>
						Export all platform data as a ZIP archive or import from a previous export
					</CardDescription>
				</CardHeader>
				<CardContent>
					<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
						<Button className="min-h-11"
							onClick={handleExportAll}
							disabled={isExportingAll}
						>
							{isExportingAll ? (
								<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
							) : (
								<Download className="h-4 w-4 mr-2" />
							)}
							Export All
						</Button>
						<Button className="min-h-11"
							variant="outline"
							onClick={() => setIsImportAllOpen(true)}
						>
							<Upload className="h-4 w-4 mr-2" />
							Import All
						</Button>
					</div>
				</CardContent>
			</Card>

			{/* Actions Card */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Settings2 className="h-5 w-5" />
						Maintenance Actions
					</CardTitle>
					<CardDescription>
						Select actions and run them sequentially
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="rounded-md ring-1 ring-foreground/5 divide-y">
						{actions.map((action) => <MaintenanceActionRow key={action.id} {...action} checked={selectedActions.has(action.id)} disabled={isAnyRunning} status={runningAction === action.id ? "running" : actionQueue.includes(action.id) ? "queued" : actionOutcomes[action.id] ?? "idle"} onToggle={() => toggleAction(action.id)} />)}
					</div>

					<Button className="min-h-11"
						onClick={handleRunSelected}
						disabled={selectedActions.size === 0 || isAnyRunning}
					>
						{isAnyRunning ? (
							<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
						) : (
							<Play className="h-4 w-4 mr-2" />
						)}
						{isAnyRunning
							? "Running..."
							: `Run Selected (${selectedActions.size})`}
					</Button>
				</CardContent>
			</Card>

			{/* Results Card */}
			<Card>
				<CardHeader>
					<CardTitle>Scan Results</CardTitle>
					<CardDescription>
						Results from the most recent scan operation
					</CardDescription>
				</CardHeader>
				<CardContent>
					{lastScanType === "none" ? (
						<div className="flex items-center justify-center py-8 text-muted-foreground">
							<p>No scan results yet. Run a scan above.</p>
						</div>
					) : lastScanType === "docs" && docsIndexResult ? (
						<DocsIndexResults result={docsIndexResult} />
					) : lastScanType === "app-deps" && appDepScanResult ? (
						<AppDepScanResults result={appDepScanResult} />
					) : null}
				</CardContent>
			</Card>

			<ImportDialog
				open={isImportAllOpen}
				onOpenChange={setIsImportAllOpen}
				entityType="all"
			/>
		</div>
	);
}
