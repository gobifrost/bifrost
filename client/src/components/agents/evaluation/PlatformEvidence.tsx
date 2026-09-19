import { AlertCircle, CheckCircle2, Clock3, Loader2 } from "lucide-react";
import { getErrorMessage } from "@/lib/api-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function PlatformStatus({ status }: { status: string }) {
	const failed = [
		"failed",
		"error",
		"recovery_required",
		"contract_failed",
		"budget_exceeded",
		"timeout",
	].includes(status);
	const passed = ["passed", "completed", "succeeded"].includes(status);
	const running = status === "running";
	const Icon = failed
		? AlertCircle
		: passed
			? CheckCircle2
			: running
				? Loader2
				: Clock3;
	return (
		<Badge
			variant="outline"
			className={
				failed
					? "text-destructive"
					: passed
						? "text-[var(--bf-success)]"
						: "text-muted-foreground"
			}
		>
			<Icon
				aria-hidden="true"
				className={`size-3 ${running ? "animate-spin motion-reduce:animate-none" : ""}`}
			/>
			{status.replaceAll("_", " ")}
		</Badge>
	);
}
export function EvidenceJson({
	value,
	label,
}: {
	value: unknown;
	label: string;
}) {
	return (
		<pre
			aria-label={label}
			tabIndex={0}
			className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 text-xs leading-relaxed"
		>
			{JSON.stringify(value ?? null, null, 2)}
		</pre>
	);
}
export function PlatformError({
	error,
	retry,
}: {
	error: unknown;
	retry?: () => void;
}) {
	if (!error) return null;
	const message = getErrorMessage(
		error,
		"The request could not be completed. Review your input and try again.",
	);
	return (
		<div
			role="alert"
			className="flex flex-wrap items-center gap-3 rounded-md border border-destructive/30 p-3 text-sm"
		>
			<AlertCircle
				aria-hidden="true"
				className="size-4 text-destructive"
			/>
			<span className="min-w-0 flex-1 break-words">{message}</span>
			{retry && (
				<Button variant="outline" onClick={retry}>
					Try again
				</Button>
			)}
		</div>
	);
}
