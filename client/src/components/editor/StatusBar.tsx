import { useEditorStore } from "@/stores/editorStore";
import { useWorkflowsStore } from "@/stores/workflowsStore";
import { useUploadProgress } from "@/stores/uploadStore";
import { useFileActivityStore } from "@/stores/fileActivityStore";
import {
	Circle,
	Workflow,
	Loader2,
	CheckCircle,
	AlertCircle,
	Radio,
	X,
} from "lucide-react";
import { useMemo, useState, useEffect } from "react";
import { Progress } from "@/components/ui/progress";

/**
 * Status bar showing file info, cursor position, and save status
 */
export function StatusBar() {
	const tabs = useEditorStore((state) => state.tabs);
	const activeTabIndex = useEditorStore((state) => state.activeTabIndex);
	const isWorkflowFile = useWorkflowsStore((state) => state.isWorkflowFile);
	const {
		state: uploadState,
		resetState: resetUpload,
		cancelUpload,
	} = useUploadProgress();
	const activeWatchers = useFileActivityStore((s) => s.activeWatchers);
	const recentPushes = useFileActivityStore((s) => s.recentPushes);

	// Tick every 30s to re-evaluate recency filtering
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		const timer = setInterval(() => setNow(Date.now()), 30_000);
		return () => clearInterval(timer);
	}, []);

	// Most recent push in last 2 minutes
	const latestPush = useMemo(() => {
		const filtered = recentPushes.filter(
			(p) => now - new Date(p.timestamp).getTime() < 120_000,
		);
		return filtered.length > 0 ? filtered[filtered.length - 1] : undefined;
	}, [recentPushes, now]);

	// Compute active tab from state
	const activeTab =
		activeTabIndex >= 0 && activeTabIndex < tabs.length
			? tabs[activeTabIndex]
			: null;

	const openFile = activeTab?.file || null;
	const cursorPosition = activeTab?.cursorPosition || { line: 1, column: 1 };
	const saveState = activeTab?.saveState || "clean";
	const selectedLanguage = activeTab?.selectedLanguage || "";

	// Check if current file is a workflow
	const isWorkflow = useMemo(() => {
		if (!openFile || openFile.type !== "file") return false;
		return isWorkflowFile(openFile.path);
	}, [openFile, isWorkflowFile]);

	// Get save status text
	const getSaveStatusText = () => {
		switch (saveState) {
			case "saving":
				return "Saving...";
			case "saved":
				return `Saved at ${new Date().toLocaleTimeString()}`;
			case "conflict":
				return "Conflict with server version";
			case "dirty":
				return "Unsaved changes";
			default:
				return null;
		}
	};

	const saveStatusText = getSaveStatusText();

	// Check if we should show upload state
	const showUploadProgress =
		uploadState.isUploading || uploadState.totalCount > 0;
	const uploadSuccessCount =
		uploadState.totalCount - uploadState.failures.length;
	const uploadProgressPercent =
		uploadState.totalCount > 0
			? Math.round(
					(uploadState.completedCount / uploadState.totalCount) * 100,
				)
			: 0;

	return (
		<div className="flex h-6 items-center justify-between border-t bg-muted/50 px-4 text-xs text-muted-foreground">
			<div className="flex items-center gap-4 flex-1 min-w-0">
				{/* Upload progress - takes priority when active */}
				{showUploadProgress ? (
					<div className="flex items-center gap-2 flex-1 min-w-0">
						{uploadState.isUploading ? (
							<>
								<Loader2 className="h-3 w-3 animate-spin shrink-0" />
								<span className="truncate max-w-32">
									{uploadState.isCancelling
										? "Cancelling..."
										: uploadState.currentFile ||
											"Preparing..."}
								</span>
								<span className="shrink-0 text-muted-foreground">
									{uploadState.completedCount}/
									{uploadState.totalCount}
								</span>
								<Progress
									value={uploadProgressPercent}
									className="h-1.5 w-24"
								/>
								{/* Cancel button */}
								<button
									onClick={cancelUpload}
									disabled={uploadState.isCancelling}
									className="p-0.5 hover:bg-muted rounded shrink-0 disabled:opacity-50"
									title="Cancel upload"
								>
									<X className="h-3 w-3" />
								</button>
							</>
						) : (
							<>
								{uploadState.failures.length === 0 ? (
									<CheckCircle className="h-3 w-3 text-green-500 shrink-0" />
								) : (
									<AlertCircle className="h-3 w-3 text-amber-500 shrink-0" />
								)}
								<span>
									{uploadState.failures.length === 0
										? `Uploaded ${uploadSuccessCount} file${uploadSuccessCount !== 1 ? "s" : ""}`
										: `Uploaded ${uploadSuccessCount}/${uploadState.totalCount} (${uploadState.failures.length} failed)`}
								</span>
								<button
									onClick={resetUpload}
									className="p-0.5 hover:bg-muted rounded shrink-0"
									title="Dismiss"
								>
									<X className="h-3 w-3" />
								</button>
							</>
						)}
					</div>
				) : (
					<>
						{/* File path */}
						{openFile && (
							<span className="font-mono truncate">
								{openFile.path}
							</span>
						)}

						{/* File type badge */}
						{openFile && openFile.path.endsWith(".py") && (
							<span
								className={`flex items-center gap-1 rounded px-2 py-0.5 font-medium shrink-0 ${
									isWorkflow
										? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
										: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400"
								}`}
							>
								{isWorkflow && <Workflow className="h-3 w-3" />}
								{isWorkflow ? "Workflow" : "Python Script"}
							</span>
						)}

						{/* Save status */}
						{saveStatusText && (
							<span
								className={`flex items-center gap-1 shrink-0 ${
									saveState === "conflict"
										? "text-orange-600"
										: saveState === "saved"
											? "text-green-600"
											: saveState === "saving"
												? "text-blue-600"
												: "text-amber-600"
								}`}
							>
								<Circle className="h-2 w-2 fill-current" />
								{saveStatusText}
							</span>
						)}
					</>
				)}
			</div>

			<div className="flex items-center gap-4 shrink-0">
				{/* CLI Watch Activity */}
				{activeWatchers.length > 0 && (
					<span className="flex items-center gap-1 text-green-600 dark:text-green-400">
						<Radio className="h-3 w-3 animate-pulse" />
						{activeWatchers.length === 1
							? `CLI watch (${activeWatchers[0].user_name})`
							: `${activeWatchers.length} CLI watchers`}
					</span>
				)}
				{!activeWatchers.length && latestPush && (
					<span className="flex items-center gap-1 text-muted-foreground">
						{latestPush.user_name} pushed {latestPush.file_count}{" "}
						files to {latestPush.prefix}
					</span>
				)}

				{/* Language */}
				{selectedLanguage && (
					<span className="capitalize">{selectedLanguage}</span>
				)}

				{/* Cursor position */}
				{openFile && (
					<span>
						Ln {cursorPosition.line}, Col {cursorPosition.column}
					</span>
				)}
			</div>
		</div>
	);
}
