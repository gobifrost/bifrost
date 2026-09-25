import { useRef, useState } from "react";
import {
	Sheet,
	SheetClose,
	SheetContent,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { ExternalLink, Copy, X } from "lucide-react";
import { copyToClipboard } from "@/lib/clipboard";
import { ExecutionDetails } from "@/pages/ExecutionDetails";

interface ExecutionDrawerProps {
	executionId: string | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onExecutionChange?: (newExecutionId: string) => void;
}

export function ExecutionDrawer({
	executionId,
	open,
	onOpenChange,
	onExecutionChange,
}: ExecutionDrawerProps) {
	const [actionsContainer, setActionsContainer] =
		useState<HTMLDivElement | null>(null);
	const [copyState, setCopyState] = useState<
		"idle" | "pending" | "success" | "error"
	>("idle");
	const copyBusy = useRef(false);
	const [previousId, setPreviousId] = useState(executionId);
	if (previousId !== executionId) {
		setPreviousId(executionId);
		setCopyState("idle");
	}

	async function handleCopy() {
		if (!executionId || copyBusy.current) return;
		copyBusy.current = true;
		setCopyState("pending");
		const copied = await copyToClipboard(executionId);
		setCopyState(copied ? "success" : "error");
		copyBusy.current = false;
	}

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent
				side="right"
				showCloseButton={false}
				className="w-full overflow-y-auto p-0 sm:max-w-xl md:max-w-2xl"
			>
				<div className="sticky top-0 z-10 space-y-3 border-b border-border bg-background px-4 py-3">
					<SheetHeader className="flex-row items-center justify-between gap-2 p-0">
						<SheetTitle className="text-sm font-medium">
							Execution details
						</SheetTitle>
						<SheetClose asChild>
							<Button
								variant="ghost"
								size="icon-lg"
								aria-label="Close execution details"
							>
								<X className="h-4 w-4" />
							</Button>
						</SheetClose>
					</SheetHeader>
					<div
						role="group"
						aria-label="Execution actions"
						className="flex min-h-11 flex-wrap items-center justify-end gap-2"
					>
						<div
							ref={setActionsContainer}
							className="flex min-h-11 flex-1 flex-wrap items-center justify-end gap-2 empty:hidden"
						/>
						<Button
							variant="ghost"
							size="icon-lg"
							onClick={() => void handleCopy()}
							disabled={!executionId || copyState === "pending"}
							aria-label="Copy execution ID"
							title="Copy execution ID"
						>
							<Copy className="h-4 w-4" />
						</Button>
						{executionId && (
							<Button variant="ghost" size="icon-lg" asChild>
								<a
									href={`/history/${executionId}`}
									target="_blank"
									rel="noopener noreferrer"
									aria-label="Open execution in new tab"
									title="Open execution in new tab"
								>
									<ExternalLink className="h-4 w-4" />
								</a>
							</Button>
						)}
					</div>
					{copyState !== "idle" && (
						<p
							role="status"
							className="text-xs text-muted-foreground"
						>
							{copyState === "pending"
								? "Copying execution ID…"
								: copyState === "success"
									? "Execution ID copied"
									: "Couldn't copy the execution ID. Try again."}
						</p>
					)}
				</div>
				{executionId && (
					<ExecutionDetails
						executionId={executionId}
						embedded
						actionsContainer={actionsContainer}
						onExecutionChange={onExecutionChange}
					/>
				)}
			</SheetContent>
		</Sheet>
	);
}
