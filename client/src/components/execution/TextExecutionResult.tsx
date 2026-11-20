import { usePresentationStore } from "@/stores/presentationStore";
import { useRef, useState } from "react";
import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { copyToClipboard } from "@/lib/clipboard";

export function TextExecutionResult({ value }: { value: string }) {
	const presentationMode = usePresentationStore((state) => state.enabled);
	const busy = useRef(false);
	const [feedback, setFeedback] = useState<{ value: string; state: "pending" | "copied" | "error" }>();
	const state = feedback?.value === value ? feedback.state : undefined;
	const copy = async () => {
		if (busy.current) return;
		busy.current = true;
		setFeedback({ value, state: "pending" });
		try {
			setFeedback({ value, state: await copyToClipboard(value) ? "copied" : "error" });
		} catch {
			setFeedback({ value, state: "error" });
		} finally { busy.current = false; }
	};
	return (
		<div className="min-w-0 overflow-hidden rounded-[var(--bf-radius-surface)] border bg-muted/50">
			<div className="flex flex-wrap items-center justify-end gap-2 border-b px-3 py-2">
				{state === "error" && (
					<p
						role="alert"
						className="min-w-0 flex-1 text-sm text-destructive"
					>
						Could not copy. Try again or select the text below.
					</p>
				)}
				{state === "copied" && (
					<p role="status" className="text-sm text-muted-foreground">
						Result copied
					</p>
				)}
				<Button
					variant="ghost"
					className="min-h-11"
					disabled={presentationMode || feedback?.state === "pending"}
					onClick={() => void copy()}
				>
					<Copy aria-hidden="true" className="size-4" />
					{state === "pending" ? "Copying…" : "Copy result"}
				</Button>
			</div>
			<pre
				data-pii-surface
				role="region"
				aria-label="Text result"
				tabIndex={0}
				className="max-h-[60vh] min-w-0 overflow-y-auto whitespace-pre-wrap p-4 font-mono text-sm leading-relaxed [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
			>
				{value}
			</pre>
		</div>
	);
}
