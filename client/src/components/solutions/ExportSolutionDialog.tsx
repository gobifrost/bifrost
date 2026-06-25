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
	const [includeValues, setIncludeValues] = useState(true);
	const [includeFiles, setIncludeFiles] = useState(true);
	const [includeData, setIncludeData] = useState(false);

	const hasBackupSelection = includeValues || includeFiles || includeData;
	const exportDisabled =
		mode === "full" && (!hasBackupSelection || password.trim() === "");

	function handleExport() {
		void onExport(
			mode,
			mode === "full" ? password : undefined,
			mode === "full"
				? { includeValues, includeFiles, includeData }
				: undefined,
		);
	}

	function handleOpenChange(next: boolean) {
		if (!next) {
			// Reset state when closing
			setMode("shareable");
			setPassword("");
			setIncludeValues(true);
			setIncludeFiles(true);
			setIncludeData(false);
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
								setIncludeValues(true);
								setIncludeFiles(true);
								setIncludeData(false);
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
									Choose which runtime state to include. Selected backup
									contents are encrypted with a password.
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
										id="export-include-values"
										checked={includeValues}
										onCheckedChange={(checked) =>
											setIncludeValues(checked === true)
										}
										className="mt-0.5 shrink-0"
									/>
									<div className="min-w-0 space-y-0.5">
										<label
											htmlFor="export-include-values"
											className="cursor-pointer text-sm font-medium leading-none"
										>
											Configuration values and secrets
										</label>
										<p className="text-xs text-muted-foreground">
											Includes configured values for this install, including
											secret values.
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
								<div className="flex items-start gap-3">
									<Checkbox
										id="export-include-data"
										checked={includeData}
										onCheckedChange={(checked) =>
											setIncludeData(checked === true)
										}
										className="mt-0.5 shrink-0"
									/>
									<div className="min-w-0 space-y-0.5">
										<label
											htmlFor="export-include-data"
											className="cursor-pointer text-sm font-medium leading-none"
										>
											Include table data
										</label>
										<p className="text-xs text-muted-foreground">
											Adds table rows to the encrypted backup payload. Table
											schemas are already included above.
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
						{isPending ? "Exporting…" : "Export"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
