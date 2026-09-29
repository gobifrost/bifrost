import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { useEffect, useRef } from "react";

export interface PricingDraft {
	provider: string;
	model: string;
	inputPrice: string;
	outputPrice: string;
	/** Optional; blank means the model has no separate cached-token rate. */
	cacheReadPrice: string;
	cacheWritePrice: string;
}

export function PricingEditDialog({
	open,
	editing,
	draft,
	pending,
	failed,
	onChange,
	onClose,
	onSave,
}: {
	open: boolean;
	editing: boolean;
	draft: PricingDraft;
	pending: boolean;
	failed: boolean;
	onChange: (draft: PricingDraft) => void;
	onClose: () => void;
	onSave: () => void;
}) {
	const errorRef = useRef<HTMLDivElement>(null);
	const focus = useDialogReturnFocus();
	const ready = Boolean(
		draft.provider.trim() &&
			draft.model.trim() &&
			draft.inputPrice.trim() &&
			draft.outputPrice.trim(),
	);

	useEffect(() => {
		if (!open || !failed) return;
		errorRef.current?.focus();
		errorRef.current?.scrollIntoView({ block: "nearest" });
	}, [failed, open]);

	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!next && !pending) onClose();
			}}
		>
			<DialogContent
				className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-lg"
				{...focus}
				onOpenAutoFocus={(event) => {
					focus.onOpenAutoFocus();
					if (failed) {
						event.preventDefault();
						errorRef.current?.focus();
						errorRef.current?.scrollIntoView({ block: "nearest" });
					}
				}}
				onEscapeKeyDown={(event) => {
					if (pending) event.preventDefault();
				}}
			>
				<DialogHeader className="shrink-0 px-6 pt-6">
					<DialogTitle>
						{editing ? "Edit model pricing" : "Add model pricing"}
					</DialogTitle>
					<DialogDescription>
						Enter token prices in US dollars per million tokens.
					</DialogDescription>
				</DialogHeader>

				<form
					className="flex min-h-0 flex-1 flex-col"
					onSubmit={(event) => {
						event.preventDefault();
						if (ready && !pending) onSave();
					}}
				>
					<div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
						<fieldset disabled={pending} className="grid min-w-0 gap-4 sm:grid-cols-2">
							{([
								{ key: "provider", label: "Provider" },
								{ key: "model", label: "Model" },
								{ key: "inputPrice", label: "Input price" },
								{ key: "outputPrice", label: "Output price" },
								{ key: "cacheReadPrice", label: "Cache read price (optional)" },
								{ key: "cacheWritePrice", label: "Cache write price (optional)" },
							] as const).map(({ key, label }) => {
								const identity = key === "provider" || key === "model";
								if (identity && editing) {
									return (
										<div key={key} className="min-w-0 space-y-2">
											<p className="text-sm font-medium">{label}</p>
											<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
												{draft[key]}
											</p>
										</div>
									);
								}

								return (
									<div key={key} className="min-w-0 space-y-2">
										<Label htmlFor={`pricing-${key}`}>{label}</Label>
										<Input
											id={`pricing-${key}`}
											className="min-h-11"
											inputMode={identity ? "text" : "decimal"}
											value={draft[key]}
											onChange={(event) =>
												onChange({
													...draft,
													[key]: event.target.value,
												})
											}
											disabled={identity && editing}
										/>
									</div>
								);
							})}
						</fieldset>

						{failed && (
							<div
								ref={errorRef}
								role="alert"
								tabIndex={-1}
								className="mt-4 rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-3 outline-none"
							>
								<p className="text-sm font-medium text-destructive">
									Could not save pricing
								</p>
								<p className="mt-1 text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
									Your entries are preserved. Check the rates and try again.
								</p>
							</div>
						)}
					</div>

					<DialogFooter className="shrink-0 border-t border-border/70 px-6 py-4">
						<Button
							type="button"
							variant="outline"
							className="w-full sm:w-auto"
							disabled={pending}
							onClick={onClose}
						>
							Cancel
						</Button>
						<Button
							type="submit"
							className="w-full sm:w-auto"
							disabled={!ready || pending}
						>
							{pending ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : null}
							{pending ? "Saving…" : "Save pricing"}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
