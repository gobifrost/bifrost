import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
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
	const remainingSeconds = Math.floor(seconds % 60);
	if (minutes < 60) {
		return `${minutes}m ${remainingSeconds}s`;
	}
	const hours = Math.floor(minutes / 60);
	const remainingMinutes = minutes % 60;
	return `${hours}h ${remainingMinutes}m`;
}

const statusStyles: Record<string, string> = {
	RUNNING: "bg-[var(--bf-info)]",
	STUCK: "bg-[var(--bf-danger)] motion-safe:animate-pulse",
	COMPLETING: "bg-[var(--bf-warning)]",
};

export function ExecutionRow({ execution }: ExecutionRowProps) {
	const reduceMotion = useReducedMotion();
	return (
		<motion.div
			initial={reduceMotion ? false : { opacity: 0, x: -20 }}
			animate={{ opacity: 1, x: 0 }}
			exit={reduceMotion ? undefined : { opacity: 0, x: 20 }}
			transition={{ duration: reduceMotion ? 0 : 0.2 }}
			className={cn(
				"flex min-w-0 flex-col gap-2 py-3 px-3 rounded-[var(--bf-radius-control)] sm:flex-row sm:items-center sm:justify-between",
				execution.status === "STUCK" && "bg-[var(--bf-danger-soft)]",
			)}
		>
			<div className="flex min-w-0 items-center gap-2">
				<div
					className={cn(
						"w-2 h-2 shrink-0 rounded-full",
						statusStyles[execution.status] || "bg-muted-foreground",
					)}
				/>
				{execution.status === "STUCK" && (
					<AlertTriangle className="w-4 h-4 shrink-0 text-[var(--bf-danger)]" />
				)}
				<span className="min-w-0 font-medium [overflow-wrap:anywhere]">
					{execution.workflow_name}
				</span>
			</div>
			<div className="flex flex-wrap items-center gap-x-4 gap-y-1 sm:shrink-0">
				<span className="text-sm text-muted-foreground">
					{execution.status}
				</span>
				<span className="text-sm tabular-nums w-16 text-right">
					{formatDuration(execution.elapsed_seconds)}
				</span>
				<Button
					variant="ghost"
					size="sm"
					className="min-h-11 ml-auto"
					asChild
				>
					<Link
						to={`/history/${execution.execution_id}`}
						aria-label={`View execution of ${execution.workflow_name}`}
					>
						<ExternalLink className="w-4 h-4 mr-1" />
						View
					</Link>
				</Button>
			</div>
		</motion.div>
	);
}
