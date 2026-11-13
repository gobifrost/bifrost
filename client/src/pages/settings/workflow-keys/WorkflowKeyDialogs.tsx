import { useEffect, useRef, useState, type RefObject } from "react";
import { Check, Copy, Loader2, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import { copyToClipboard } from "@/lib/clipboard";
import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
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
import { Combobox } from "@/components/ui/combobox";
import {
	Alert,
	AlertDescription,
	AlertTitle,
} from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export interface WorkflowKeyFormValues {
	workflowId: string;
	expiresInDays: string;
	description: string;
	isGlobal: boolean;
}

const defaultFormValues: WorkflowKeyFormValues = {
	workflowId: "",
	expiresInDays: "90",
	description: "",
	isGlobal: false,
};

export function WorkflowKeyCreateDialog({
	open,
	onOpenChange,
	onSubmit,
	pending,
	error,
	canSubmit,
	workflowOptions,
	returnFocusRef,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSubmit: (values: WorkflowKeyFormValues) => void | Promise<void>;
	pending: boolean;
	error: string | null;
	canSubmit: boolean;
	workflowOptions: Array<{ value: string; label: string }>;
	returnFocusRef?: RefObject<HTMLElement | null>;
}) {
	const [formValues, setFormValues] = useState(defaultFormValues);
	const errorRef = useRef<HTMLParagraphElement>(null);
	const focus = useDialogReturnFocus(returnFocusRef);

	useEffect(() => {
		if (!open || !error) return;
		errorRef.current?.focus();
		errorRef.current?.scrollIntoView({ block: "nearest" });
	}, [error, open]);

	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!pending) onOpenChange(next);
			}}
		>
			<DialogContent
				{...focus}
				className="flex max-h-[90dvh] flex-col overflow-hidden"
				onOpenAutoFocus={(event) => {
					focus.onOpenAutoFocus();
					if (error) {
						event.preventDefault();
						errorRef.current?.focus();
					}
				}}
				onEscapeKeyDown={(event) => {
					if (pending) event.preventDefault();
				}}
				onPointerDownOutside={(event) => {
					if (pending) event.preventDefault();
				}}
			>
				<DialogHeader className="shrink-0">
					<DialogTitle>Create Workflow API Key</DialogTitle>
					<DialogDescription>
						Generate a new API key for external systems to trigger
						workflows. The key will only be shown once.
					</DialogDescription>
				</DialogHeader>

				<form
					className="flex min-h-0 flex-1 flex-col"
					onSubmit={(event) => {
						event.preventDefault();
						if (pending || !canSubmit) return;
						void onSubmit(formValues);
					}}
				>
					<div className="min-h-0 flex-1 space-y-4 overflow-y-auto py-4">
						<fieldset
							disabled={pending || !canSubmit}
							className="min-w-0 space-y-4"
						>
							<div className="space-y-2">
								<Label htmlFor="workflow-key-description">
									Description
									<span className="text-destructive ml-1">
										*
									</span>
								</Label>
								<Input
									id="workflow-key-description"
									maxLength={32}
									placeholder="Production API key for CRM"
									value={formValues.description}
									onChange={(event) =>
										setFormValues((current) => ({
											...current,
											description: event.target.value,
										}))
									}
								/>
								<p className="text-xs text-muted-foreground">
									Brief description, up to 32 characters
								</p>
							</div>

							<div className="flex items-start gap-3">
								<Switch
									id="workflow-key-global"
									checked={formValues.isGlobal}
									onCheckedChange={(checked) =>
										setFormValues((current) => ({
											...current,
											isGlobal: checked === true,
											workflowId:
												checked === true
													? ""
													: current.workflowId,
										}))
									}
								/>
								<div className="space-y-0.5">
									<Label
										htmlFor="workflow-key-global"
										className="cursor-pointer font-medium"
									>
										Global Key
									</Label>
									<p className="text-xs text-muted-foreground">
										Allow this key to execute any workflow
									</p>
								</div>
							</div>

							{!formValues.isGlobal && (
								<div className="space-y-2">
									<Label htmlFor="workflow-key-workflow">
										Workflow{" "}
										<span className="text-destructive">
											*
										</span>
									</Label>
									<Combobox
										id="workflow-key-workflow"
										value={formValues.workflowId}
										onValueChange={(value) =>
											setFormValues((current) => ({
												...current,
												workflowId: value,
											}))
										}
										options={workflowOptions}
										placeholder="Select a workflow..."
										searchPlaceholder="Search workflows..."
										emptyText="No workflows found."
									/>
									<p className="text-xs text-muted-foreground">
										Restrict this key to a specific
										workflow
									</p>
								</div>
							)}

							<div className="space-y-2">
								<Label htmlFor="workflow-key-expires">
									Expiration (days)
								</Label>
								<Combobox
									id="workflow-key-expires"
									value={formValues.expiresInDays}
									onValueChange={(value) =>
										setFormValues((current) => ({
											...current,
											expiresInDays: value,
										}))
									}
									options={[
										{ value: "0", label: "Never" },
										{ value: "30", label: "30 days" },
										{ value: "60", label: "60 days" },
										{ value: "90", label: "90 days" },
										{ value: "180", label: "180 days" },
										{ value: "365", label: "365 days" },
									]}
									placeholder="Select expiration"
								/>
								<p className="text-xs text-muted-foreground">
									How long until the key expires
								</p>
							</div>
						</fieldset>

						{error && (
							<p
								ref={errorRef}
								role="alert"
								tabIndex={-1}
								className="rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive outline-none [overflow-wrap:anywhere]"
							>
								{error}
							</p>
						)}
					</div>

					<DialogFooter className="shrink-0 border-t pt-4">
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							onClick={() => onOpenChange(false)}
							disabled={pending}
						>
							Cancel
						</Button>
						<Button
							type="submit"
							className="min-h-11"
							disabled={pending || !canSubmit}
						>
							{pending ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
									Creating...
								</>
							) : (
								"Create API Key"
							)}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

export function WorkflowKeyRevealDialog({
	open,
	onOpenChange,
	rawKey,
	description,
	scopeLabel,
	workflowId,
	expiresLabel,
	returnFocusRef,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	rawKey: string;
	description?: string | null;
	scopeLabel: string;
	workflowId?: string | null;
	expiresLabel: string;
	returnFocusRef?: RefObject<HTMLElement | null>;
}) {
	const [copied, setCopied] = useState(false);
	const [copyError, setCopyError] = useState<string | null>(null);
	const errorRef = useRef<HTMLParagraphElement>(null);
	const focus = useDialogReturnFocus(returnFocusRef, true);

	useEffect(() => {
		if (!open || !copyError) return;
		errorRef.current?.focus();
		errorRef.current?.scrollIntoView({ block: "nearest" });
	}, [copyError, open]);

	const handleCopy = async () => {
		if (!rawKey) return;
		setCopyError(null);
		const success = await copyToClipboard(rawKey);
		if (!success) {
			setCopied(false);
			setCopyError(
				"Could not copy the API key. Copy it manually from the text field below.",
			);
			return;
		}
		setCopied(true);
		toast.success("API key copied to clipboard");
	};

	return (
		<Dialog
			open={open}
			onOpenChange={onOpenChange}
		>
			<DialogContent
				{...focus}
				className="flex max-h-[90dvh] flex-col overflow-hidden"
				onOpenAutoFocus={(event) => {
					focus.onOpenAutoFocus();
					if (copyError) {
						event.preventDefault();
						errorRef.current?.focus();
					}
				}}
			>
				<DialogHeader className="shrink-0">
					<DialogTitle className="flex items-center gap-2">
						<Check className="h-5 w-5 text-success" />
						API Key Created Successfully
					</DialogTitle>
					<DialogDescription>
						This is the only time you'll be able to view this key.
						Copy it now and store it securely.
					</DialogDescription>
				</DialogHeader>

				<div className="min-h-0 flex-1 space-y-4 overflow-y-auto py-4">
					<Alert variant="destructive">
						<AlertTriangle className="h-4 w-4" />
						<AlertTitle>Important</AlertTitle>
						<AlertDescription>
							This key will not be shown again. Make sure to copy
							it before closing this dialog.
						</AlertDescription>
					</Alert>

					<div className="space-y-2">
						<Label>Your API Key</Label>
						<div className="flex gap-2">
							<Input
								value={rawKey}
								readOnly
								className="min-h-11 min-w-0 font-mono text-sm"
							/>
							<Button
								type="button"
								variant="outline"
								size="icon"
								className="h-11 w-11 shrink-0 rounded-[var(--bf-radius-control)]"
								onClick={() => {
									void handleCopy();
								}}
								aria-label={copied ? "Copied API key" : "Copy API key"}
							>
								{copied ? (
									<Check className="h-4 w-4" />
								) : (
									<Copy className="h-4 w-4" />
								)}
							</Button>
						</div>
					</div>

					{copyError && (
						<p
							ref={errorRef}
							role="alert"
							tabIndex={-1}
							className="rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive outline-none [overflow-wrap:anywhere]"
						>
							{copyError}
						</p>
					)}

					<div className="space-y-2">
						<Label>Usage Example</Label>
						<div className="rounded-[var(--bf-radius-control)] bg-muted p-3 ring-1 ring-foreground/5">
							<pre className="overflow-x-auto text-xs">
								<code>{`curl -X POST ${
									window.location.protocol
								}//${window.location.host}/api/endpoints/${workflowId ?? "{workflowId}"} \\
  -H "X-Bifrost-Key: ${rawKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"input": "your data"}'`}</code>
							</pre>
						</div>
					</div>

					{description && (
						<div className="space-y-2">
							<Label>Description</Label>
							<p className="text-sm text-muted-foreground">
								{description}
							</p>
						</div>
					)}

					<div className="grid grid-cols-2 gap-4 text-sm">
						<div className="space-y-1">
							<Label className="text-xs text-muted-foreground">
								Scope
							</Label>
							<p>
								{scopeLabel === "Global" ? (
									<Badge variant="default">Global</Badge>
								) : (
									<Badge variant="outline" className="font-mono">
										{scopeLabel}
									</Badge>
								)}
							</p>
						</div>
						<div className="space-y-1">
							<Label className="text-xs text-muted-foreground">
								Expires
							</Label>
							<p>{expiresLabel}</p>
						</div>
					</div>
				</div>

				<DialogFooter className="shrink-0 border-t pt-4">
					<Button
						type="button"
						className="min-h-11"
						onClick={() => onOpenChange(false)}
					>
						I've Copied the Key
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

export function WorkflowKeyRevokeDialog({
	open,
	onOpenChange,
	keyLabel,
	error,
	pending,
	returnFocusRef,
	preferFallback,
	fallbackRef,
	onConfirm,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	keyLabel: string;
	error: string | null;
	pending: boolean;
	returnFocusRef?: RefObject<HTMLElement | null>;
	preferFallback: boolean;
	fallbackRef?: RefObject<HTMLElement | null>;
	onConfirm: () => void;
}) {
	const focus = useDialogReturnFocus(
		preferFallback ? fallbackRef : returnFocusRef,
		preferFallback,
	);
	const errorRef = useRef<HTMLParagraphElement>(null);

	useEffect(() => {
		if (!open || !error) return;
		errorRef.current?.focus();
		errorRef.current?.scrollIntoView({ block: "nearest" });
	}, [error, open]);

	return (
		<AlertDialog
			open={open}
			onOpenChange={(next) => {
				if (!pending) onOpenChange(next);
			}}
		>
				<AlertDialogContent
					{...focus}
					onEscapeKeyDown={(event) => {
						if (pending) event.preventDefault();
					}}
				>
				<AlertDialogHeader>
					<AlertDialogTitle>Revoke API Key?</AlertDialogTitle>
					<AlertDialogDescription>
						This will immediately revoke the API key ending in{" "}
						<strong className="font-mono">{keyLabel}</strong>. Any
						systems using this key will no longer be able to execute
						workflows. This action cannot be undone.
					</AlertDialogDescription>
				</AlertDialogHeader>

				{error && (
					<p
						ref={errorRef}
						role="alert"
						tabIndex={-1}
						className="rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive outline-none [overflow-wrap:anywhere]"
					>
						{error}
					</p>
				)}

				<AlertDialogFooter>
					<AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
					<AlertDialogAction
						variant="destructive"
						disabled={pending}
						onClick={(event) => {
							event.preventDefault();
							onConfirm();
						}}
					>
						{pending ? "Revoking..." : "Revoke Key"}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
