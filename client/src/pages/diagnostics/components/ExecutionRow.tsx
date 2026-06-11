import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { AlertTriangle, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface ExecutionRowData {
	execution_id: string;
	workflow_name: string;
	status: "RUNNING" | "STUCK" | "COMPLETING";
	elapsed_seconds: number;
}

interface ExecutionRowProps {
	execution: ExecutionRowData;
}

/**
 * Format duration from seconds to human-readable string
 */
function formatDuration(seconds: number): string {
	if (seconds < 60) {
		return `${Math.floor(seconds)}s`;
	}
	const minutes = Math.floor(seconds / 60);
	const remainingSeconds = seconds % 60;
	if (minutes < 60) {
		return `${minutes}m ${remainingSeconds}s`;
	}
	const hours = Math.floor(minutes / 60);
	const remainingMinutes = minutes % 60;
	return `${hours}h ${remainingMinutes}m`;
}

const statusStyles: Record<string, string> = {
	RUNNING: "bg-blue-500",
	STUCK: "bg-red-500 animate-pulse",
	COMPLETING: "bg-yellow-500",
};

export function ExecutionRow({ execution }: ExecutionRowProps) {
	return (
		<motion.div
			initial={{ opacity: 0, x: -20 }}
			animate={{ opacity: 1, x: 0 }}
			exit={{ opacity: 0, x: 20 }}
			transition={{ duration: 0.2 }}
			className={cn(
				"flex items-center justify-between py-2 px-3 rounded-lg",
				execution.status === "STUCK" && "bg-red-50 dark:bg-red-950/30"
			)}
		>
			<div className="flex items-center gap-2">
				<div
					className={cn(
						"w-2 h-2 rounded-full",
						statusStyles[execution.status] || "bg-gray-400"
					)}
				/>
				{execution.status === "STUCK" && (
					<AlertTriangle className="w-4 h-4 text-red-500" />
				)}
				<span className="font-medium">{execution.workflow_name}</span>
			</div>
			<div className="flex items-center gap-4">
				<span className="text-sm text-muted-foreground">
					{execution.status}
				</span>
				<span className="text-sm tabular-nums w-16 text-right">
					{formatDuration(execution.elapsed_seconds)}
				</span>
				<Button variant="ghost" size="sm" asChild>
					<Link to={`/history/${execution.execution_id}`}>
						<ExternalLink className="w-4 h-4 mr-1" />
						View
					</Link>
				</Button>
			</div>
		</motion.div>
	);
}
