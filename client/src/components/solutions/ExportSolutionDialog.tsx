import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import type { SolutionExportOptions } from "@/services/solutions";

export interface ExportSolutionDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** Called when the user confirms. Presentational — no network calls here. */
	onExport: (
		mode: "shareable" | "full",
		password?: string,
		options?: SolutionExportOptions,
	) => void | Promise<void>;
	/** When true, the Export button is disabled and shows a spinner. */
	isPending?: boolean;
}

/**
 * Presentational dialog for choosing the solution export mode.
 *
 * - "Package" (default): definition only, safe to share.
 * - "Backup": package plus selected runtime state. Selected runtime state is
 *   password-encrypted.
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
	const [includeConfigs, setIncludeConfigs] = useState(true);
	const [includeSecrets, setIncludeSecrets] = useState(false);
	const [includeTables, setIncludeTables] = useState(false);
	const [includeFiles, setIncludeFiles] = useState(true);

	const hasBackupSelection =
		includeConfigs || includeSecrets || includeTables || includeFiles;
	const exportDisabled =
		mode === "full" && (!hasBackupSelection || password.trim() === "");
	const submitLabel =
		mode === "full"
			? isPending
				? "Queueing..."
				: "Queue backup"
			: isPending
				? "Exporting..."
				: "Export";

	function handleExport() {
		void onExport(
			mode,
			mode === "full" ? password : undefined,
			mode === "full"
				? { includeConfigs, includeSecrets, includeTables, includeFiles }
				: undefined,
		);
	}

	function handleOpenChange(next: boolean) {
		if (!next) {
			// Reset state when closing
			setMode("shareable");
			setPassword("");
			setIncludeConfigs(true);
			setIncludeSecrets(false);
			setIncludeTables(false);
			setIncludeFiles(true);
		}
		onOpenChange(next);
	}

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>Export Solution</DialogTitle>
					<DialogDescription>
						Choose how to export this Solution. Definitions, table schemas,
						config declarations, file-location declarations, and source files
						are included in both modes.
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-4">
					<RadioGroup
						value={mode}
						onValueChange={(v) => {
							setMode(v as "shareable" | "full");
							if (v === "shareable") {
								setPassword("");
								setIncludeConfigs(true);
								setIncludeSecrets(false);
								setIncludeTables(false);
								setIncludeFiles(true);
							}
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
								aria-label="Package"
								className="mt-0.5 shrink-0"
							/>
							<span className="min-w-0">
								<span className="block text-sm font-medium">Package</span>
								<span className="mt-0.5 block text-xs text-muted-foreground">
									Definitions only. Omits runtime values, file payloads, and
									table rows. Safe to share with others or publish.
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
								aria-label="Backup"
								className="mt-0.5 shrink-0"
							/>
							<span className="min-w-0">
								<span className="block text-sm font-medium">Backup</span>
								<span className="mt-0.5 block text-xs text-muted-foreground">
									Choose which runtime state to include. Backups run in the
									background, are encrypted with a password, and are kept for 7
									days.
								</span>
							</span>
						</label>
					</RadioGroup>

					{mode === "full" && (
						<>
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

							<div className="space-y-3 rounded-lg border p-3">
								<p className="text-sm font-medium">Backup contents</p>
								<div className="flex items-start gap-3">
									<Checkbox
										id="export-include-configs"
										checked={includeConfigs}
										onCheckedChange={(checked) =>
											setIncludeConfigs(checked === true)
										}
										className="mt-0.5 shrink-0"
									/>
									<div className="min-w-0 space-y-0.5">
										<label
											htmlFor="export-include-configs"
											className="cursor-pointer text-sm font-medium leading-none"
										>
											Config values
										</label>
										<p className="text-xs text-muted-foreground">
											Includes non-secret configured values for this install.
										</p>
									</div>
								</div>
								<div className="flex items-start gap-3">
									<Checkbox
										id="export-include-secrets"
										checked={includeSecrets}
										onCheckedChange={(checked) =>
											setIncludeSecrets(checked === true)
										}
										className="mt-0.5 shrink-0"
									/>
									<div className="min-w-0 space-y-0.5">
										<label
											htmlFor="export-include-secrets"
											className="cursor-pointer text-sm font-medium leading-none"
										>
											Secrets
										</label>
										<p className="text-xs text-muted-foreground">
											Includes secret config values in the encrypted backup.
										</p>
									</div>
								</div>
								<div className="flex items-start gap-3">
									<Checkbox
										id="export-include-tables"
										checked={includeTables}
										onCheckedChange={(checked) =>
											setIncludeTables(checked === true)
										}
										className="mt-0.5 shrink-0"
									/>
									<div className="min-w-0 space-y-0.5">
										<label
											htmlFor="export-include-tables"
											className="cursor-pointer text-sm font-medium leading-none"
										>
											Table data
										</label>
										<p className="text-xs text-muted-foreground">
											Adds table rows to the encrypted backup payload. Table
											schemas are already included above.
										</p>
									</div>
								</div>
								<div className="flex items-start gap-3">
									<Checkbox
										id="export-include-files"
										checked={includeFiles}
										onCheckedChange={(checked) =>
											setIncludeFiles(checked === true)
										}
										className="mt-0.5 shrink-0"
									/>
									<div className="min-w-0 space-y-0.5">
										<label
											htmlFor="export-include-files"
											className="cursor-pointer text-sm font-medium leading-none"
										>
											Solution-owned files
										</label>
										<p className="text-xs text-muted-foreground">
											Includes file payloads owned by this Solution.
										</p>
									</div>
								</div>
								{!hasBackupSelection && (
									<p className="text-xs text-destructive">
										Select at least one backup content type.
									</p>
								)}
							</div>
						</>
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
						{submitLabel}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
