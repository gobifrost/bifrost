import { useState, useEffect, useCallback, useRef } from "react";
import { Package, RefreshCw, Download, ArrowUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import {
	checkUpdates,
	installPackage,
	listPackages,
	type InstalledPackage,
	type PackageUpdate,
} from "@/hooks/usePackages";
import { webSocketService } from "@/services/websocket";
import { useEditorStore } from "@/stores/editorStore";
import { useExecutionStreamStore } from "@/stores/executionStreamStore";
import { handleProgressEvent } from "./packageProgress";
import type { ProgressState } from "./packageProgress";

/**
 * Package management panel for installing and managing Python packages.
 * Shows installed packages, available updates, and provides installation UI.
 *
 * Install flow: API updates requirements.txt, broadcasts to workers.
 * Workers pip install, recycle processes, stream progress via WebSocket.
 */
export function PackagePanel() {
	const [packages, setPackages] = useState<InstalledPackage[]>([]);
	const [updates, setUpdates] = useState<PackageUpdate[]>([]);
	const [packageName, setPackageName] = useState("");
	const [version, setVersion] = useState("");
	const [isInstalling, setIsInstalling] = useState(false);
	const [isLoadingPackages, setIsLoadingPackages] = useState(false);
	const [isCheckingUpdates, setIsCheckingUpdates] = useState(false);
	const [isConnected, setIsConnected] = useState(false);
	const [currentInstallationId, setCurrentInstallationId] = useState<
		string | null
	>(null);

	// Track whether we've already handled completion for this installation.
	// Multiple workers publish progress; we only fire completeExecution once.
	const completionHandledRef = useRef(false);

	// Ref for installationId so WebSocket callbacks can read it synchronously.
	// React state updates are batched; the closure would capture stale values.
	const currentInstallationIdRef = useRef<string | null>(null);

	// Tracks the last progress line we appended so identical consecutive lines
	// from multiple workers can be collapsed to a single entry.
	const lastProgressLineRef = useRef<string>("");

	const setCurrentStreamingExecutionId = useEditorStore(
		(state) => state.setCurrentStreamingExecutionId,
	);
	const appendTerminalOutput = useEditorStore(
		(state) => state.appendTerminalOutput,
	);
	const clearStream = useExecutionStreamStore((state) => state.clearStream);
	const streamState = useExecutionStreamStore((state) =>
		currentInstallationId
			? state.streams[currentInstallationId]
			: undefined,
	);

	// Connect to WebSocket for real-time package installation logs
	useEffect(() => {
		const init = async () => {
			try {
				await webSocketService.connect();
				setIsConnected(webSocketService.isConnected());
			} catch (error) {
				console.error("[PackagePanel] Failed to connect:", error);
				setIsConnected(false);
			}
		};

		init();
	}, []);

	const loadPackages = useCallback(async () => {
		setIsLoadingPackages(true);
		try {
			const data = await listPackages();
			setPackages(data?.packages || []);
		} catch (error) {
			console.error("Failed to load packages:", error);
			toast.error("Failed to load packages");
		} finally {
			setIsLoadingPackages(false);
		}
	}, []);

	// Subscribe to shared package:install channel for streaming progress
	useEffect(() => {
		if (!isConnected) {
			return;
		}

		const packageChannel = "package:install";
		webSocketService.subscribe(packageChannel);

		const unsubscribeProgress = webSocketService.onPackageProgress((p) => {
			const id = currentInstallationIdRef.current;
			if (!id) return;

			const progressState: ProgressState = {
				lastLine: lastProgressLineRef.current,
				completionHandled: completionHandledRef.current,
			};

			const store = useExecutionStreamStore.getState();
			const triggered = handleProgressEvent(p, progressState, store, id);

			// Sync mutated state back to refs
			lastProgressLineRef.current = progressState.lastLine;
			completionHandledRef.current = progressState.completionHandled;

			if (triggered) {
				loadPackages();
			}
		});

		return () => {
			unsubscribeProgress();
			webSocketService.unsubscribe(packageChannel);
		};
	}, [isConnected, loadPackages]);

	// Load packages on mount. setState only fires after the awaited fetch
	// resolves — wrapping in a void IIFE keeps the synchronous body free of
	// setState calls.
	useEffect(() => {
		void (async () => {
			await loadPackages();
		})();
	}, [loadPackages]);

	// Handle stream completion (move logs to terminal output and cleanup).
	// All cleanup is deferred to a microtask so the synchronous effect body
	// does not directly call setState (which would trigger the
	// set-state-in-effect rule).
	useEffect(() => {
		if (!streamState?.isComplete || !currentInstallationId) {
			return;
		}

		queueMicrotask(() => {
			const completion =
				streamState.status === "Success"
					? {
							message: "Installation complete",
							level: "SUCCESS" as const,
						}
					: { message: "Installation failed", level: "ERROR" as const };

			appendTerminalOutput({
				loggerOutput: [
					...streamState.streamingLogs.map((log) => ({
						...log,
						source: "package" as const,
					})),
					{
						level: completion.level,
						message: completion.message,
						timestamp: new Date().toISOString(),
						source: "system" as const,
					},
				],
				variables: {},
				status: streamState.status,
				error: streamState.error,
			});

			const executionId = currentInstallationId;
			setCurrentInstallationId(null);
			currentInstallationIdRef.current = null;
			setCurrentStreamingExecutionId(null);
			setIsInstalling(false);

			if (executionId) {
				clearStream(executionId);
			}

			void loadPackages();
		});
	}, [
		streamState?.isComplete,
		currentInstallationId,
		streamState?.status,
		streamState?.streamingLogs,
		streamState?.error,
		appendTerminalOutput,
		clearStream,
		setCurrentStreamingExecutionId,
		loadPackages,
	]);

	async function handleCheckForUpdates() {
		setIsCheckingUpdates(true);
		try {
			const data = await checkUpdates();
			setUpdates(data?.updates_available || []);
			if ((data?.updates_available?.length ?? 0) > 0) {
				toast.info(
					`${data?.updates_available?.length} update(s) available`,
				);
			} else {
				toast.success("All packages are up to date");
			}
		} catch (error) {
			console.error("Failed to check updates:", error);
			toast.error("Failed to check for updates");
		} finally {
			setIsCheckingUpdates(false);
		}
	}

	async function handleInstallPackage() {
		if (!packageName.trim()) {
			toast.error("Please enter a package name");
			return;
		}

		const pkgName = packageName.trim();
		const pkgVersion = version.trim();

		const installationId = `package-install-${Date.now()}`;
		// Set ref synchronously so WebSocket callbacks can read it immediately
		currentInstallationIdRef.current = installationId;
		setCurrentInstallationId(installationId);
		completionHandledRef.current = false;
		lastProgressLineRef.current = "";
		setIsInstalling(true);

		try {
			const store = useExecutionStreamStore.getState();
			store.startStreaming(installationId, "Running");
			setCurrentStreamingExecutionId(installationId);

			await installPackage(pkgName, pkgVersion || undefined);

			setPackageName("");
			setVersion("");

			if (!isConnected) {
				store.appendLog(installationId, {
					level: "INFO",
					message: "Package installation queued (no WebSocket connection)",
					timestamp: new Date().toISOString(),
				});
				store.completeExecution(installationId, undefined, "Success");
				setIsInstalling(false);
				setCurrentInstallationId(null);
				currentInstallationIdRef.current = null;
				setTimeout(() => loadPackages(), 5000);
			}
		} catch (error) {
			console.error("Failed to start installation:", error);
			const store = useExecutionStreamStore.getState();
			store.appendLog(installationId, {
				level: "ERROR",
				message: `Failed to start installation: ${error instanceof Error ? error.message : "Unknown error"}`,
				timestamp: new Date().toISOString(),
			});
			store.completeExecution(installationId, undefined, "Failed");
			setIsInstalling(false);
			setCurrentInstallationId(null);
			currentInstallationIdRef.current = null;
		}
	}

	async function handleInstallFromRequirements() {
		const installationId = `package-install-${Date.now()}`;
		// Set ref synchronously so WebSocket callbacks can read it immediately
		currentInstallationIdRef.current = installationId;
		setCurrentInstallationId(installationId);
		completionHandledRef.current = false;
		lastProgressLineRef.current = "";
		setIsInstalling(true);

		try {
			const store = useExecutionStreamStore.getState();
			store.startStreaming(installationId, "Running");
			setCurrentStreamingExecutionId(installationId);

			await installPackage();

			if (!isConnected) {
				store.appendLog(installationId, {
					level: "INFO",
					message: "Package installation queued (no WebSocket connection)",
					timestamp: new Date().toISOString(),
				});
				store.completeExecution(installationId, undefined, "Success");
				setIsInstalling(false);
				setCurrentInstallationId(null);
				currentInstallationIdRef.current = null;
				setTimeout(() => loadPackages(), 5000);
			}
		} catch (error) {
			console.error("Failed to start installation:", error);
			const store = useExecutionStreamStore.getState();
			store.appendLog(installationId, {
				level: "ERROR",
				message: `Failed to start installation: ${error instanceof Error ? error.message : "Unknown error"}`,
				timestamp: new Date().toISOString(),
			});
			store.completeExecution(installationId, undefined, "Failed");
			setIsInstalling(false);
			setCurrentInstallationId(null);
			currentInstallationIdRef.current = null;
		}
	}

	function getUpdateForPackage(
		pkg: InstalledPackage,
	): PackageUpdate | undefined {
		return updates.find((u) => u.name === pkg.name);
	}

	return (
		<div className="flex h-full flex-col">
			{/* Header with underline */}
			<div className="flex items-center justify-between border-b p-2">
				<h2 className="text-sm font-semibold flex items-center gap-2">
					<Package className="h-4 w-4" />
					Packages
				</h2>
				<Button
					variant="ghost"
					size="sm"
					onClick={handleCheckForUpdates}
					disabled={isCheckingUpdates || isLoadingPackages}
					className="h-7 px-2"
				>
					{isCheckingUpdates ? (
						<Loader2 className="h-4 w-4 animate-spin" />
					) : (
						<RefreshCw className="h-4 w-4" />
					)}
				</Button>
			</div>

			{/* Install Package Form */}
			<div className="p-3 space-y-2 border-b">
				<div>
					<Label htmlFor="package-name" className="text-xs">
						Package Name
					</Label>
					<Input
						id="package-name"
						placeholder="e.g., requests"
						value={packageName}
						onChange={(e) => setPackageName(e.target.value)}
						disabled={isInstalling}
						className="h-8 text-sm"
						onKeyDown={(e) => {
							if (e.key === "Enter") {
								handleInstallPackage();
							}
						}}
					/>
				</div>
				<div>
					<Label htmlFor="package-version" className="text-xs">
						Version (optional)
					</Label>
					<Input
						id="package-version"
						placeholder="e.g., 2.31.0"
						value={version}
						onChange={(e) => setVersion(e.target.value)}
						disabled={isInstalling}
						className="h-8 text-sm"
						onKeyDown={(e) => {
							if (e.key === "Enter") {
								handleInstallPackage();
							}
						}}
					/>
				</div>
				<Button
					onClick={handleInstallPackage}
					disabled={isInstalling || !packageName.trim()}
					size="sm"
					className="w-full h-8"
				>
					{isInstalling ? (
						<>
							<Loader2 className="mr-2 h-3 w-3 animate-spin" />
							Installing...
						</>
					) : (
						<>
							<Download className="mr-2 h-3 w-3" />
							Install
						</>
					)}
				</Button>
				<Button
					onClick={handleInstallFromRequirements}
					disabled={isInstalling}
					variant="outline"
					size="sm"
					className="w-full h-8"
				>
					{isInstalling ? (
						<>
							<Loader2 className="mr-2 h-3 w-3 animate-spin" />
							Installing...
						</>
					) : (
						<>
							<Download className="mr-2 h-3 w-3" />
							requirements.txt
						</>
					)}
				</Button>
			</div>

			{/* Installed Packages List */}
			<div className="flex-1 overflow-auto">
				<div className="px-2 py-1 text-xs font-medium text-muted-foreground border-b bg-muted/30">
					Installed ({packages.length})
				</div>
				{isLoadingPackages ? (
					<div className="flex items-center justify-center py-8">
						<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
					</div>
				) : packages.length === 0 ? (
					<div className="text-xs text-muted-foreground text-center py-8">
						No packages installed
					</div>
				) : (
					<div>
						{packages.map((pkg) => {
							const update = getUpdateForPackage(pkg);
							return (
								<div
									key={pkg.name}
									className="flex items-center justify-between px-3 py-2 border-b hover:bg-muted/50"
								>
									<div>
										<div className="font-medium text-xs">
											{pkg.name}
										</div>
										<div className="text-[10px] text-muted-foreground">
											v{pkg.version}
										</div>
									</div>
									{update && (
										<div className="flex items-center gap-1 text-[10px] text-blue-600 dark:text-blue-400">
											<ArrowUp className="h-3 w-3" />
											{update.latest_version}
										</div>
									)}
								</div>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
}
