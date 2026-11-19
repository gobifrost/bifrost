import { usePresentationStore } from "@/stores/presentationStore";
import { useRef, useState } from "react";
import { Check, Copy, Eye, TreeDeciduous } from "lucide-react";
import { Button } from "@/components/ui/button";
import { copyToClipboard } from "@/lib/clipboard";

interface InputDisplayToolbarProps {
	inputData: Record<string, unknown> | unknown[];
	view: "pretty" | "tree";
	showToggle: boolean;
	description?: string;
	onViewChange: (view: "pretty" | "tree") => void;
}

export function InputDisplayToolbar({
	inputData,
	view,
	showToggle,
	description,
	onViewChange,
}: InputDisplayToolbarProps) {
	const presentationMode = usePresentationStore((state) => state.enabled);
	const [result, setResult] = useState<{
		input: typeof inputData;
		status: "idle" | "pending" | "copied" | "error";
	}>({ input: inputData, status: "idle" });
	const copyState = result.input === inputData ? result.status : "idle";
	const busy = useRef(false);
	const setCopyState = (status: typeof result.status) =>
		setResult({ input: inputData, status });
	const copy = async () => {
		if (busy.current) return;
		busy.current = true;
		setCopyState("pending");
		try {
			const success = await copyToClipboard(
				JSON.stringify(inputData, null, 2),
			);
			setCopyState(success ? "copied" : "error");
		} catch {
			setCopyState("error");
		} finally {
			busy.current = false;
		}
	};
	return (
		<div className="min-w-0 space-y-2">
			<div className="flex flex-wrap items-center justify-between gap-2">
				{description && (
					<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
						{description}
					</p>
				)}
				<div className="ml-auto flex flex-wrap gap-2">
					<Button
						type="button"
						variant="ghost"
						className="min-h-11 sm:min-h-8 h-auto px-2 py-1 text-sm whitespace-normal"
						disabled={
							presentationMode || result.status === "pending"
						}
						onClick={copy}
					>
						{copyState === "copied" ? (
							<Check className="size-3.5 text-[var(--bf-success)]" />
						) : (
							<Copy className="size-3.5" />
						)}
						{copyState === "pending"
							? "Copying…"
							: copyState === "copied"
								? "Copied!"
								: copyState === "error"
									? "Retry copy"
									: "Copy"}
					</Button>
					{showToggle && (
						<Button
							type="button"
							variant="ghost"
							className="min-h-11 sm:min-h-8 h-auto px-2 py-1 text-sm whitespace-normal"
							onClick={() =>
								onViewChange(
									view === "tree" ? "pretty" : "tree",
								)
							}
						>
							{view === "tree" ? (
								<Eye className="size-3.5" />
							) : (
								<TreeDeciduous className="size-3.5" />
							)}
							{view === "tree" ? "Pretty View" : "Tree View"}
						</Button>
					)}
				</div>
			</div>
			{copyState !== "idle" && (
				<p
					role="status"
					className={`text-sm ${copyState === "error" ? "text-destructive" : "text-muted-foreground"}`}
				>
					{copyState === "error"
						? "Couldn’t copy. All data is ready to retry."
						: copyState === "copied"
							? "All data copied to clipboard."
							: "Copying all data…"}
				</p>
			)}
		</div>
	);
}
