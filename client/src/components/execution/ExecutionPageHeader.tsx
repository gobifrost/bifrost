import type { ReactNode } from "react";
import {
	ArrowLeft,
	Code2,
	Copy,
	Loader2,
	RefreshCw,
	Workflow,
	XCircle,
} from "lucide-react";
import { RunDetailHeading } from "./RunDetailHeading";
import { Button } from "@/components/ui/button";

interface ExecutionPageHeaderProps {
	name: string;
	status: ReactNode;
	onBack: () => void;
	backLabel?: "History" | "Usage";
	onCopyId: () => void;
	onOpenEditor?: (() => void) | undefined;
	onRerun?: (() => void) | undefined;
	onCancel?: (() => void) | undefined;
	openingEditor: boolean;
	rerunning: boolean;
}

export function ExecutionPageHeader({
	name,
	status,
	onBack,
	backLabel = "History",
	onCopyId,
	onOpenEditor,
	onRerun,
	onCancel,
	openingEditor,
	rerunning,
}: ExecutionPageHeaderProps) {
	return (
		<header className="@container shrink-0 space-y-3 border-b bg-background px-4 pb-4 pt-2 sm:px-6 lg:px-8">
			<div className="flex items-center justify-between gap-3">
				<Button
					type="button"
					variant="ghost"
					className="min-h-11 px-2"
					aria-label={`Back to ${backLabel.toLowerCase()}`}
					onClick={onBack}
				>
					<ArrowLeft aria-hidden="true" className="size-4" />
					{backLabel}
				</Button>
				<Button
					type="button"
					variant="ghost"
					className="size-11"
					aria-label="Copy execution ID"
					title="Copy execution ID"
					onClick={onCopyId}
				>
					<Copy aria-hidden="true" className="size-4" />
				</Button>
			</div>
			<RunDetailHeading
				title={
					<span className="flex items-start gap-3">
						<span
							aria-hidden="true"
							className="grid size-11 shrink-0 place-items-center rounded-[var(--bf-radius-control)] border border-primary/20 bg-primary/10 text-primary"
						>
							<Workflow className="size-6" />
						</span>
						<span className="min-w-0 self-center [overflow-wrap:anywhere]">
							{name}
						</span>
					</span>
				}
				metadata={status}
				actionsLabel="Execution actions"
				actions={
					onOpenEditor || onRerun || onCancel ? (
						<>
							{onOpenEditor && (
								<Button
									type="button"
									variant="outline"
									className="min-h-11 min-w-0"
									onClick={onOpenEditor}
									disabled={openingEditor}
								>
									{openingEditor ? (
										<Loader2
											aria-hidden="true"
											className="size-4 animate-spin motion-reduce:animate-none"
										/>
									) : (
										<Code2
											aria-hidden="true"
											className="size-4"
										/>
									)}
									Editor
								</Button>
							)}
							{onRerun && (
								<Button
									type="button"
									className="min-h-11 min-w-0"
									onClick={onRerun}
									disabled={rerunning}
								>
									{rerunning ? (
										<Loader2
											aria-hidden="true"
											className="size-4 animate-spin motion-reduce:animate-none"
										/>
									) : (
										<RefreshCw
											aria-hidden="true"
											className="size-4"
										/>
									)}
									Rerun
								</Button>
							)}
							{onCancel && (
								<Button
									type="button"
									variant="outline"
									className="min-h-11 min-w-0"
									onClick={onCancel}
								>
									<XCircle
										aria-hidden="true"
										className="size-4"
									/>
									Cancel
								</Button>
							)}
						</>
					) : undefined
				}
			/>
		</header>
	);
}
