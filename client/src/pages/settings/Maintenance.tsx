/**
 * Workspace Maintenance Settings
 *
 * Platform admin page for managing workspace maintenance operations.
 * Provides tools for documentation indexing and app dependency scanning.
 */

import { useState, useEffect } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
	AlertCircle,
	CheckCircle2,
	Loader2,
	AlertTriangle,
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

interface DocsIndexResponse {
	status: string;
	files_indexed: number;
	files_unchanged: number;
	files_deleted: number;
	duration_ms: number;
	message: string | null;
}

interface AppDependencyIssue {
	app_id: string;
	app_name: string;
	app_slug: string;
	file_path: string;
	dependency_type: string;
	dependency_id: string;
}

interface AppDependencyScanResponse {
	apps_scanned: number;
	files_scanned: number;
	dependencies_rebuilt: number;
	issues_found: number;
	issues: AppDependencyIssue[];
	notification_created: boolean;
}

type ScanResultType = "none" | "docs" | "app-deps";

export function Maintenance() {
	// Checklist state
	const [selectedActions, setSelectedActions] = useState<Set<string>>(new Set());
	const [runningAction, setRunningAction] = useState<string | null>(null);
	const [completedActions, setCompletedActions] = useState<Set<string>>(new Set());
	const [actionQueue, setActionQueue] = useState<string[]>([]);

	// Results
	const [lastScanType, setLastScanType] = useState<ScanResultType>("none");
	const [docsIndexResult, setDocsIndexResult] =
		useState<DocsIndexResponse | null>(null);
	const [appDepScanResult, setAppDepScanResult] =
		useState<AppDependencyScanResponse | null>(null);

	const [isExportingAll, setIsExportingAll] = useState(false);
	const [isImportAllOpen, setIsImportAllOpen] = useState(false);

	const isAnyRunning = runningAction !== null;

	const finishAction = (actionId: string) => {
		setCompletedActions((prev) => new Set([...prev, actionId]));
		setRunningAction(null);
	};

	const handleDocsIndex = async () => {
		setRunningAction("docs");

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
			finishAction("docs");
		}
	};

	const handleAppDepScan = async () => {
		setRunningAction("app-deps");

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
			finishAction("app-deps");
		}
	};

	const handleReimport = async () => {
		setRunningAction("reimport");

		try {
			const response = await authFetch("/api/maintenance/reimport", {
				method: "POST",
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("Reimport failed", {
					description: errorData.detail || "Unknown error",
				});
				finishAction("reimport");
				return;
			}

			const { job_id } = await response.json();
			toast.info("Reimport started", {
				description: "Re-importing entities from repository...",
			});

			// Poll for job completion
			const poll = async () => {
				for (let i = 0; i < 120; i++) {
					await new Promise((r) => setTimeout(r, 2000));
					try {
						const res = await authFetch(`/api/jobs/${job_id}`);
						const result = await res.json();
						if (result.status === "success") {
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
						// Network error, keep polling
					}
				}
				toast.warning("Reimport timed out", {
					description: "Job may still be running. Check scheduler logs.",
				});
			};

			await poll();
		} catch (err) {
			toast.error("Reimport failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			finishAction("reimport");
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

		queueMicrotask(() => {
			const [next, ...rest] = actionQueue;
			setActionQueue(rest);

			const handlers: Record<string, () => Promise<void>> = {
				docs: handleDocsIndex,
				"app-deps": handleAppDepScan,
				reimport: handleReimport,
			};

			void handlers[next]?.();
		});
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [runningAction, actionQueue]);

	const handleRunSelected = () => {
		if (selectedActions.size === 0) return;
		setCompletedActions(new Set());
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
					<div className="flex items-center gap-4">
						<Button
							onClick={handleExportAll}
							disabled={isExportingAll}
						>
							{isExportingAll ? (
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
							) : (
								<Download className="h-4 w-4 mr-2" />
							)}
							Export All
						</Button>
						<Button
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
						{actions.map((action) => {
							const Icon = action.icon;
							const isRunning = runningAction === action.id;
							const isCompleted = completedActions.has(action.id);
							const isQueued = actionQueue.includes(action.id);

							return (
								<div key={action.id}>
									<label
										htmlFor={`action-${action.id}`}
										className={`flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/50 ${
											isRunning ? "bg-muted/50" : ""
										}`}
									>
										{isRunning ? (
											<Loader2 className="h-4 w-4 animate-spin text-primary flex-shrink-0" />
										) : isCompleted ? (
											<CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0" />
										) : (
											<Checkbox
												id={`action-${action.id}`}
												checked={selectedActions.has(action.id)}
												onCheckedChange={() => toggleAction(action.id)}
												disabled={isAnyRunning}
											/>
										)}
										<Icon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
										<div className="min-w-0">
											<p className="text-sm font-medium">
												{action.label}
												{isQueued && (
													<span className="text-xs text-muted-foreground ml-2">queued</span>
												)}
											</p>
											<p className="text-xs text-muted-foreground">
												{action.description}
											</p>
										</div>
									</label>
								</div>
							);
						})}
					</div>

					<Button
						onClick={handleRunSelected}
						disabled={selectedActions.size === 0 || isAnyRunning}
					>
						{isAnyRunning ? (
							<Loader2 className="h-4 w-4 mr-2 animate-spin" />
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

function DocsIndexResults({ result }: { result: DocsIndexResponse }) {
	const isSuccess = result.status === "complete";
	const isSkipped = result.status === "skipped";

	const formatDuration = (ms: number) => {
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	};

	return (
		<div className="space-y-4">
			{/* Summary */}
			<div className="flex items-center gap-4 flex-wrap">
				{isSuccess ? (
					<div className="flex items-center gap-2 text-green-600">
						<CheckCircle2 className="h-5 w-5" />
						<span className="font-medium">Indexing complete</span>
					</div>
				) : isSkipped ? (
					<div className="flex items-center gap-2 text-amber-600">
						<AlertCircle className="h-5 w-5" />
						<span className="font-medium">Indexing skipped</span>
					</div>
				) : (
					<div className="flex items-center gap-2 text-destructive">
						<AlertTriangle className="h-5 w-5" />
						<span className="font-medium">Indexing failed</span>
					</div>
				)}
			</div>

			{/* Stats */}
			{isSuccess && (
				<div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
					<div className="rounded-lg bg-muted/50 p-3 text-center ring-1 ring-foreground/5">
						<div className="text-2xl font-bold">
							{result.files_indexed}
						</div>
						<div className="text-xs text-muted-foreground">
							Indexed
						</div>
					</div>
					<div className="rounded-lg bg-muted/50 p-3 text-center ring-1 ring-foreground/5">
						<div className="text-2xl font-bold">
							{result.files_unchanged}
						</div>
						<div className="text-xs text-muted-foreground">
							Unchanged
						</div>
					</div>
					<div className="rounded-lg bg-muted/50 p-3 text-center ring-1 ring-foreground/5">
						<div className="text-2xl font-bold">
							{result.files_deleted}
						</div>
						<div className="text-xs text-muted-foreground">
							Deleted
						</div>
					</div>
					<div className="rounded-lg bg-muted/50 p-3 text-center ring-1 ring-foreground/5">
						<div className="text-2xl font-bold">
							{formatDuration(result.duration_ms)}
						</div>
						<div className="text-xs text-muted-foreground">
							Duration
						</div>
					</div>
				</div>
			)}

			{/* Message */}
			{result.message && (
				<p className="text-sm text-muted-foreground">
					{result.message}
				</p>
			)}
		</div>
	);
}

function AppDepScanResults({ result }: { result: AppDependencyScanResponse }) {
	const hasIssues = result.issues_found > 0;

	// Group issues by app
	const issuesByApp = result.issues.reduce(
		(acc, issue) => {
			const key = issue.app_slug;
			if (!acc[key]) {
				acc[key] = {
					app_name: issue.app_name,
					app_slug: issue.app_slug,
					issues: [],
				};
			}
			acc[key].issues.push(issue);
			return acc;
		},
		{} as Record<
			string,
			{
				app_name: string;
				app_slug: string;
				issues: AppDependencyIssue[];
			}
		>,
	);

	return (
		<div className="space-y-4">
			{/* Summary */}
			<div className="flex items-center gap-4 flex-wrap">
				{hasIssues ? (
					<div className="flex items-center gap-2 text-amber-600">
						<AlertTriangle className="h-5 w-5" />
						<span className="font-medium">
							{result.issues_found} broken reference
							{result.issues_found !== 1 ? "s" : ""}
						</span>
					</div>
				) : (
					<div className="flex items-center gap-2 text-green-600">
						<CheckCircle2 className="h-5 w-5" />
						<span className="font-medium">
							All dependencies valid
						</span>
					</div>
				)}
				<Badge variant="secondary">
					{result.apps_scanned} app
					{result.apps_scanned !== 1 ? "s" : ""} scanned
				</Badge>
				<Badge variant="outline">
					{result.files_scanned} file
					{result.files_scanned !== 1 ? "s" : ""}
				</Badge>
				<Badge variant="outline">
					{result.dependencies_rebuilt} dependenc
					{result.dependencies_rebuilt !== 1 ? "ies" : "y"} rebuilt
				</Badge>
			</div>

			{/* Issues by app */}
			{hasIssues && (
				<div className="space-y-2">
					<h4 className="text-sm font-medium text-muted-foreground">
						Missing workflow references:
					</h4>
					<div className="max-h-64 overflow-y-auto rounded-md bg-muted/50 p-3 space-y-3 ring-1 ring-foreground/5">
						{Object.entries(issuesByApp).map(
							([appSlug, { app_name, issues }]) => (
								<div key={appSlug} className="space-y-1">
									<div className="flex items-center gap-2 text-sm font-medium">
										<AppWindow className="h-4 w-4 text-muted-foreground flex-shrink-0" />
										<span>{app_name}</span>
										<Badge
											variant="outline"
											className="text-xs px-1.5 py-0"
										>
											{appSlug}
										</Badge>
									</div>
									<ul className="ml-6 space-y-1">
										{issues.map((issue, idx) => (
											<li
												key={`${issue.dependency_id}-${idx}`}
												className="text-sm text-muted-foreground"
											>
												<span className="font-mono text-xs">
													{issue.file_path}
												</span>
												<span className="mx-1">→</span>
												<code className="bg-destructive/10 text-destructive px-1 py-0.5 rounded text-xs">
													{issue.dependency_type}:{" "}
													{issue.dependency_id}
												</code>
											</li>
										))}
									</ul>
								</div>
							),
						)}
					</div>
				</div>
			)}
		</div>
	);
}
