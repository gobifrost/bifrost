/**
 * Email Configuration Settings
 *
 * Configure a workflow to handle email sending (password resets, notifications, etc.).
 * Flow: Select Workflow → Validate Signature → Save
 */

import { useState } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { WorkflowSelector } from "@/components/forms/WorkflowSelector";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
	Loader2,
	Mail,
	CheckCircle2,
	AlertCircle,
	Trash2,
	Zap,
	Info,
} from "lucide-react";
import { $api } from "@/lib/api-client";
import { EmailTestDialog } from "@/components/settings/EmailTestDialog";
import { useAuth } from "@/contexts/AuthContext";

export function Email() {
	// Form state
	const [selectedWorkflowId, setSelectedWorkflowId] = useState<string>("");

	// UI state
	const [saving, setSaving] = useState(false);
	const [validating, setValidating] = useState(false);
	const [validationResult, setValidationResult] = useState<{
		valid: boolean;
		message: string;
		missing_params?: string[];
		extra_required_params?: string[];
	} | null>(null);
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
	const [showTestDialog, setShowTestDialog] = useState(false);
	const { user: currentUser } = useAuth();

	// Load current configuration
	const {
		data: config,
		isLoading: configLoading,
		refetch,
	} = $api.useQuery("get", "/api/admin/email/config", undefined, {
		staleTime: 5 * 60 * 1000,
	});

	// Mutations
	const saveMutation = $api.useMutation("post", "/api/admin/email/config");
	const deleteMutation = $api.useMutation(
		"delete",
		"/api/admin/email/config",
	);
	const validateMutation = $api.useMutation(
		"post",
		"/api/admin/email/validate/{workflow_id}",
	);

	// Update form when config loads. Adjust during render with a previous-
	// value sentinel rather than a setState-in-effect.
	const [prevConfigWorkflowId, setPrevConfigWorkflowId] = useState<
		string | undefined
	>(undefined);
	if (config && prevConfigWorkflowId !== config.workflow_id) {
		setPrevConfigWorkflowId(config.workflow_id);
		setSelectedWorkflowId(config.workflow_id);
	}

	// Reset validation when workflow changes
	const handleWorkflowChange = (workflowId: string | undefined) => {
		setSelectedWorkflowId(workflowId ?? "");
		setValidationResult(null);
	};

	// Test workflow: validate + (optionally) dispatch a real test send
	const runTest = async (recipient: string | null) => {
		if (!selectedWorkflowId) {
			toast.error("Please select a workflow");
			return;
		}

		setValidating(true);
		setValidationResult(null);

		try {
			const result = await validateMutation.mutateAsync({
				params: { path: { workflow_id: selectedWorkflowId } },
				body: recipient ? { recipient } : null,
			});

			setValidationResult({
				valid: result.valid,
				message: result.message,
				missing_params: result.missing_params ?? undefined,
				extra_required_params:
					result.extra_required_params ?? undefined,
			});

			if (!result.valid) {
				toast.error("Workflow validation failed", {
					description: result.message,
				});
				return;
			}

			if (recipient) {
				if (result.email_sent) {
					toast.success(`Test email dispatched to ${recipient}`);
					setShowTestDialog(false);
				} else {
					toast.error("Test email failed", {
						description: result.send_error ?? "Unknown send error",
					});
				}
			} else {
				toast.success("Workflow validated", {
					description: result.message,
				});
			}
		} catch (error) {
			const message =
				error instanceof Error ? error.message : "Unknown error";
			setValidationResult({ valid: false, message });
			toast.error("Test failed", { description: message });
		} finally {
			setValidating(false);
		}
	};

	const handleOpenTestDialog = () => {
		if (!selectedWorkflowId) {
			toast.error("Please select a workflow");
			return;
		}
		setShowTestDialog(true);
	};

	// Save configuration
	const handleSave = async () => {
		if (!selectedWorkflowId) {
			toast.error("Please select a workflow");
			return;
		}

		if (!validationResult?.valid) {
			toast.error("Please test the workflow first");
			return;
		}

		setSaving(true);
		try {
			await saveMutation.mutateAsync({
				body: { workflow_id: selectedWorkflowId },
			});

			toast.success("Email configuration saved", {
				description: "Email sending is now enabled",
			});

			setValidationResult(null);
			refetch();
		} catch (error) {
			toast.error("Failed to save configuration", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	// Delete configuration
	const handleDelete = async () => {
		setSaving(true);
		setShowDeleteConfirm(false);

		try {
			await deleteMutation.mutateAsync({});

			setSelectedWorkflowId("");
			setValidationResult(null);

			toast.success("Email configuration removed", {
				description: "Email sending is now disabled",
			});

			refetch();
		} catch (error) {
			toast.error("Failed to remove configuration", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	if (configLoading) {
		return (
			<div className="flex items-center justify-center py-12">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	// Check if we have an unchanged config (already saved, same workflow selected)
	const isUnchangedConfig =
		config?.workflow_id && config.workflow_id === selectedWorkflowId;
	const canSave =
		!saving &&
		selectedWorkflowId &&
		(validationResult?.valid || isUnchangedConfig);

	return (
		<div className="space-y-6">
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Mail className="h-5 w-5" />
						<CardTitle>Email Configuration</CardTitle>
					</div>
					<CardDescription>
						Configure a workflow to handle email sending for system
						emails (password resets, notifications, etc.).
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{/* Status Banner */}
					{config ? (
						<div className="rounded-lg border bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900 p-4">
							<div className="flex items-center justify-between">
								<div className="flex items-center gap-2">
									<CheckCircle2 className="h-4 w-4 text-green-600" />
									<span className="text-sm font-medium text-green-800 dark:text-green-200">
										Email Enabled
									</span>
								</div>
								<Button
									variant="ghost"
									size="sm"
									onClick={() => setShowDeleteConfirm(true)}
									className="text-destructive hover:text-destructive"
								>
									<Trash2 className="h-4 w-4 mr-1" />
									Remove
								</Button>
							</div>
							<p className="mt-1 text-sm text-green-700 dark:text-green-300">
								Using workflow: {config.workflow_name}
							</p>
						</div>
					) : (
						<div className="rounded-lg border bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900 p-4">
							<div className="flex items-center gap-2">
								<AlertCircle className="h-4 w-4 text-amber-600" />
								<span className="text-sm font-medium text-amber-800 dark:text-amber-200">
									Email Not Configured
								</span>
							</div>
							<p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
								Select a workflow below to enable email sending.
							</p>
						</div>
					)}

					{/* Workflow Selection */}
					<div className="space-y-2">
						<Label htmlFor="workflow">Email Workflow</Label>
						<div className="flex gap-2">
							<WorkflowSelector
								value={selectedWorkflowId || undefined}
								onChange={handleWorkflowChange}
								variant="combobox"
								allowClear={false}
								placeholder="Select a workflow"
								className="flex-1"
							/>
							<Button
								variant="secondary"
								onClick={handleOpenTestDialog}
								disabled={validating || !selectedWorkflowId}
							>
								{validating ? (
									<>
										<Loader2 className="h-4 w-4 mr-2 animate-spin" />
										Testing...
									</>
								) : validationResult?.valid ? (
									<>
										<CheckCircle2 className="h-4 w-4 mr-2 text-green-600" />
										Valid
									</>
								) : validationResult?.valid === false ? (
									<>
										<AlertCircle className="h-4 w-4 mr-2 text-destructive" />
										Invalid
									</>
								) : (
									<>
										<Zap className="h-4 w-4 mr-2" />
										Test
									</>
								)}
							</Button>
						</div>

						{/* Validation Result Details */}
						{validationResult && !validationResult.valid && (
							<div className="mt-2 p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm">
								<p className="text-destructive font-medium">
									{validationResult.message}
								</p>
								{validationResult.missing_params &&
									validationResult.missing_params.length >
										0 && (
										<p className="mt-1 text-muted-foreground">
											Missing:{" "}
											{validationResult.missing_params.join(
												", ",
											)}
										</p>
									)}
								{validationResult.extra_required_params &&
									validationResult.extra_required_params
										.length > 0 && (
										<p className="mt-1 text-muted-foreground">
											Extra required:{" "}
											{validationResult.extra_required_params.join(
												", ",
											)}
										</p>
									)}
							</div>
						)}
					</div>

					{/* Save Button */}
					<div className="flex flex-col items-end gap-2 pt-4">
						{!canSave &&
							selectedWorkflowId &&
							!validationResult?.valid &&
							!isUnchangedConfig && (
								<p className="text-xs text-muted-foreground">
									Test the workflow before saving
								</p>
							)}
						<Button onClick={handleSave} disabled={!canSave}>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Saving...
								</>
							) : (
								"Save Configuration"
							)}
						</Button>
					</div>
				</CardContent>
			</Card>

			{/* Required Signature Info Card */}
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Info className="h-5 w-5" />
						<CardTitle className="text-base">
							Required Workflow Signature
						</CardTitle>
					</div>
				</CardHeader>
				<CardContent className="space-y-4">
					<p className="text-sm text-muted-foreground">
						Your email workflow must accept these parameters:
					</p>

					<div className="space-y-3">
						<div>
							<h4 className="text-sm font-medium mb-2">
								Required Parameters
							</h4>
							<ul className="space-y-1 text-sm text-muted-foreground ml-4 list-disc">
								<li>
									<code className="text-foreground">
										recipient
									</code>{" "}
									(str) - Email address to send to
								</li>
								<li>
									<code className="text-foreground">
										subject
									</code>{" "}
									(str) - Email subject line
								</li>
								<li>
									<code className="text-foreground">
										body
									</code>{" "}
									(str) - Plain text email body
								</li>
							</ul>
						</div>

						<div>
							<h4 className="text-sm font-medium mb-2">
								Optional Parameters
							</h4>
							<ul className="space-y-1 text-sm text-muted-foreground ml-4 list-disc">
								<li>
									<code className="text-foreground">
										html_body
									</code>{" "}
									(str | None) - HTML version of email body
								</li>
							</ul>
						</div>
					</div>

					<div className="mt-4 p-3 rounded-lg bg-muted/50">
						<p className="text-sm font-medium mb-2">
							Example Workflow Decorator
						</p>
						<pre className="text-xs font-mono text-muted-foreground overflow-x-auto">
							{`@workflow(name="send_email")
def send_email(
    recipient: str,
    subject: str,
    body: str,
    html_body: str | None = None
):
    # Your email implementation here
    ...`}
						</pre>
					</div>
				</CardContent>
			</Card>

			{/* Delete Confirmation Dialog */}
			<Dialog
				open={showDeleteConfirm}
				onOpenChange={setShowDeleteConfirm}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Remove Email Configuration</DialogTitle>
						<DialogDescription>
							Are you sure you want to remove the email workflow
							configuration? This will disable email sending for
							system emails until reconfigured.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setShowDeleteConfirm(false)}
							disabled={saving}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={handleDelete}
							disabled={saving}
						>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Removing...
								</>
							) : (
								"Remove Configuration"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<EmailTestDialog
				open={showTestDialog}
				onOpenChange={setShowTestDialog}
				currentUserEmail={currentUser?.email ?? ""}
				onTest={(recipient) => runTest(recipient)}
				isPending={validating}
			/>
		</div>
	);
}
