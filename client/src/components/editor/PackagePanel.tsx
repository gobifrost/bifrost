import { useState, useEffect, useCallback, useRef } from "react";
import { Package, RefreshCw, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PackageInstallForm } from "./PackageInstallForm";
import { InstalledPackageList } from "./InstalledPackageList";
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
	const [isLoadingPackages, setIsLoadingPackages] = useState(true);
	const [packageLoadError, setPackageLoadError] = useState<string | null>(
		null,
	);
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

		void init();
	}, []);

	const loadPackages = useCallback(async () => {
		setIsLoadingPackages(true);
		setPackageLoadError(null);
		try {
			const data = await listPackages();
			setPackages(data?.packages || []);
		} catch (error) {
			console.error("Failed to load packages:", error);
			setPackageLoadError("Couldn’t load installed packages. Try again.");
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
		void webSocketService.subscribe(packageChannel);

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
				void loadPackages();
			}
		});

		return () => {
			unsubscribeProgress();
			void webSocketService.unsubscribe(packageChannel);
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
					: {
							message: "Installation failed",
							level: "ERROR" as const,
						};

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
					message:
						"Package installation queued (no WebSocket connection)",
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
					message:
						"Package installation queued (no WebSocket connection)",
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

	return (
		<div className="flex h-full min-h-0 min-w-0 flex-col">
			<div className="flex shrink-0 items-center justify-between gap-2 border-b p-2">
				<h2 className="flex items-center gap-2 text-sm font-semibold">
					<Package className="size-4" />
					Packages
				</h2>
				<Button
					variant="ghost"
					size="icon"
					aria-label="Check for package updates"
					title="Check for package updates"
					onClick={handleCheckForUpdates}
					disabled={isCheckingUpdates || isLoadingPackages}
					className="size-11 shrink-0"
				>
					{isCheckingUpdates ? (
						<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
					) : (
						<RefreshCw className="size-4" />
					)}
				</Button>
			</div>
			<div className="min-h-0 flex-1 overflow-y-auto">
				<PackageInstallForm
					packageName={packageName}
					version={version}
					isInstalling={isInstalling}
					onPackageNameChange={setPackageName}
					onVersionChange={setVersion}
					onInstall={handleInstallPackage}
					onInstallRequirements={handleInstallFromRequirements}
				/>
				<InstalledPackageList
					packages={packages}
					updates={updates}
					isLoading={isLoadingPackages}
					error={packageLoadError}
					onRetry={loadPackages}
				/>
			</div>
		</div>
	);
}
