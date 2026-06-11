import { motion, AnimatePresence } from "framer-motion";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PrettyInputDisplay } from "./PrettyInputDisplay";
import { SafeHTMLRenderer } from "./SafeHTMLRenderer";

interface ExecutionResultPanelProps {
	/** The execution result (can be object, string, or null) */
	result?: unknown;
	/** How to render the result: 'json', 'html', 'text', or undefined for auto-detect */
	resultType?: string | null;
	/** Workflow name for HTML title */
	workflowName?: string;
	/** Whether the result is still loading */
	isLoading?: boolean;
	/** Optional className for the card */
	className?: string;
	/** Render without the Card wrapper (drawer / compact contexts) */
	embedded?: boolean;
}

export function ExecutionResultPanel({
	result,
	resultType,
	workflowName,
	isLoading = false,
	className,
	embedded = false,
}: ExecutionResultPanelProps) {
	const renderResult = () => {
		// JSON result type
		if (
			resultType === "json" &&
			typeof result === "object" &&
			result !== null
		) {
			return (
				<PrettyInputDisplay
					inputData={result as Record<string, unknown>}
					showToggle={true}
					defaultView="pretty"
				/>
			);
		}

		// HTML result type
		if (resultType === "html" && typeof result === "string") {
			return (
				<SafeHTMLRenderer
					html={result}
					title={
						workflowName
							? `${workflowName} - Execution Result`
							: "Execution Result"
					}
				/>
			);
		}

		// Text result type
		if (resultType === "text" && typeof result === "string") {
			return (
				<pre className="whitespace-pre-wrap font-mono text-sm bg-muted p-4 rounded">
					{result}
				</pre>
			);
		}

		// Auto-detect: object without explicit type
		if (!resultType && typeof result === "object" && result !== null) {
			return (
				<PrettyInputDisplay
					inputData={result as Record<string, unknown>}
					showToggle={true}
					defaultView="pretty"
				/>
			);
		}

		// String without explicit type
		if (!resultType && typeof result === "string") {
			return (
				<pre className="whitespace-pre-wrap font-mono text-sm bg-muted p-4 rounded">
					{result}
				</pre>
			);
		}

		// Primitive values
		if (result !== null && result !== undefined) {
			return (
				<pre className="whitespace-pre-wrap font-mono text-sm bg-muted p-4 rounded">
					{String(result)}
				</pre>
			);
		}

		return null;
	};

	const body = (
		<AnimatePresence mode="wait">
			{isLoading ? (
						<motion.div
							key="loading"
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							transition={{ duration: 0.2 }}
							className="space-y-3"
						>
							<Skeleton className="h-4 w-full" />
							<Skeleton className="h-4 w-3/4" />
							<Skeleton className="h-4 w-5/6" />
						</motion.div>
					) : result === null || result === undefined ? (
						<motion.div
							key="empty"
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							transition={{ duration: 0.2 }}
							className="text-center text-muted-foreground py-8"
						>
							No result returned
						</motion.div>
					) : (
						<motion.div
							key="content"
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							transition={{ duration: 0.2 }}
						>
							{renderResult()}
						</motion.div>
					)}
		</AnimatePresence>
	);

	if (embedded) {
		return (
			<div className={className}>
				<h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
					Result
				</h4>
				{body}
			</div>
		);
	}

	return (
		<Card className={className}>
			<CardHeader className="pb-3">
				<CardTitle>Result</CardTitle>
			</CardHeader>
			<CardContent>{body}</CardContent>
		</Card>
	);
}
