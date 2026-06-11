import { motion, AnimatePresence } from "framer-motion";
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
	/** Optional className for the section */
	className?: string;
}

export function ExecutionResultPanel({
	result,
	resultType,
	workflowName,
	isLoading = false,
	className,
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
				<pre className="whitespace-pre-wrap font-mono text-xs rounded-lg bg-muted/50 ring-1 ring-foreground/5 px-3 py-2.5">
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
				<pre className="whitespace-pre-wrap font-mono text-xs rounded-lg bg-muted/50 ring-1 ring-foreground/5 px-3 py-2.5">
					{result}
				</pre>
			);
		}

		// Primitive values
		if (result !== null && result !== undefined) {
			return (
				<pre className="whitespace-pre-wrap font-mono text-xs rounded-lg bg-muted/50 ring-1 ring-foreground/5 px-3 py-2.5">
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

	// Inspector section idiom: compact small-caps header, content carries its
	// own single step-1 surface (PrettyInputDisplay group / pre block).
	return (
		<section className={className}>
			<h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
				Result
			</h4>
			{body}
		</section>
	);
}
