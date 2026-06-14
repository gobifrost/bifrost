import { useState } from "react";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

export interface ExportSolutionDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** Called when the user confirms. Presentational — no network calls here. */
	onExport: (
		mode: "shareable" | "full",
		password?: string,
	) => void | Promise<void>;
	/** When true, the Export button is disabled and shows a spinner. */
	isPending?: boolean;
}

/**
 * Presentational dialog for choosing the solution export mode.
 *
 * - "Shareable bundle" (default): strips secret config values, safe to share.
 * - "Full backup": includes encrypted secrets; requires a password.
 *
 * Network calls are the caller's responsibility (onExport prop).
 */
export function ExportSolutionDialog({
	open,
	onOpenChange,
	onExport,
	isPending = false,
}: ExportSolutionDialogProps) {
	const [mode, setMode] = useState<"shareable" | "full">("shareable");
	const [password, setPassword] = useState("");

	const exportDisabled = mode === "full" && password.trim() === "";

	function handleExport() {
		void onExport(mode, mode === "full" ? password : undefined);
	}

	function handleOpenChange(next: boolean) {
		if (!next) {
			// Reset state when closing
			setMode("shareable");
			setPassword("");
		}
		onOpenChange(next);
	}

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>Export Solution</DialogTitle>
					<DialogDescription>
						Choose how to export this Solution. The shareable bundle is safe to
						distribute — secrets are omitted. A full backup retains encrypted
						secret values and requires a password to install.
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-4">
					<RadioGroup
						value={mode}
						onValueChange={(v) => {
							setMode(v as "shareable" | "full");
							if (v === "shareable") setPassword("");
						}}
						className="gap-3"
					>
						<label
							htmlFor="mode-shareable"
							className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 hover:bg-muted/50 has-[[data-state=checked]]:border-primary"
						>
							<RadioGroupItem
								id="mode-shareable"
								value="shareable"
								aria-label="Shareable bundle"
								className="mt-0.5 shrink-0"
							/>
							<span className="min-w-0">
								<span className="block text-sm font-medium">
									Shareable bundle
								</span>
								<span className="mt-0.5 block text-xs text-muted-foreground">
									Secret config values are excluded. Safe to share with others
									or publish.
								</span>
							</span>
						</label>

						<label
							htmlFor="mode-full"
							className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 hover:bg-muted/50 has-[[data-state=checked]]:border-primary"
						>
							<RadioGroupItem
								id="mode-full"
								value="full"
								aria-label="Full backup"
								className="mt-0.5 shrink-0"
							/>
							<span className="min-w-0">
								<span className="block text-sm font-medium">Full backup</span>
								<span className="mt-0.5 block text-xs text-muted-foreground">
									Includes encrypted secret values. Requires a password to
									install. Keep this file private.
								</span>
							</span>
						</label>
					</RadioGroup>

					{mode === "full" && (
						<div className="space-y-1.5">
							<Label htmlFor="export-password">
								Password{" "}
								<span className="text-destructive" aria-hidden>
									*
								</span>
							</Label>
							<Input
								id="export-password"
								type="password"
								required
								value={password}
								onChange={(e) => setPassword(e.target.value)}
								placeholder="Set a password for this backup"
								autoComplete="new-password"
							/>
							<p className="text-xs text-muted-foreground">
								You will need this password when installing the backup on
								another instance.
							</p>
						</div>
					)}
				</div>

				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => handleOpenChange(false)}
					>
						Cancel
					</Button>
					<Button
						type="button"
						disabled={exportDisabled || isPending}
						onClick={handleExport}
					>
						{isPending && (
							<Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
						)}
						{isPending ? "Exporting…" : "Export"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
