import { useState, useMemo } from "react";
import {
	Key,
	Plus,
	Trash2,
	RefreshCw,
	Loader2,
	Copy,
	Check,
	Globe,
	Workflow as WorkflowIcon,
	AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
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
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
	useWorkflowKeys,
	useCreateWorkflowKey,
	useRevokeWorkflowKey,
} from "@/hooks/useWorkflowKeys";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { toast } from "sonner";
import type { WorkflowKeyResponse } from "@/services/workflowKeys";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];

interface CreateFormData {
	workflowId: string;
	expiresInDays: string;
	description: string;
	isGlobal: boolean;
}

export function WorkflowKeys() {
	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [isRevokeDialogOpen, setIsRevokeDialogOpen] = useState(false);
	const [isKeyDisplayDialogOpen, setIsKeyDisplayDialogOpen] = useState(false);
	const [selectedKey, setSelectedKey] = useState<
		WorkflowKeyResponse | undefined
	>();
	const [createdKey, setCreatedKey] = useState<
		WorkflowKeyResponse | undefined
	>();
	const [copied, setCopied] = useState(false);
	const [formData, setFormData] = useState<CreateFormData>({
		workflowId: "",
		expiresInDays: "90",
		description: "",
		isGlobal: false,
	});

	const {
		data: keys,
		isFetching,
		refetch,
	} = useWorkflowKeys({ includeRevoked: false });
	const { data: workflowsData } = useWorkflowsMetadata();
	const createMutation = useCreateWorkflowKey();
	const revokeMutation = useRevokeWorkflowKey();

	// Get workflow names for dropdown - only show endpoint-enabled non-public workflows
	const workflows = useMemo(() => {
		if (!workflowsData?.workflows) return [];
		return workflowsData.workflows
			.filter(
				(w: WorkflowMetadata) =>
					w.endpoint_enabled && !w.public_endpoint,
			)
			.sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
	}, [workflowsData]);


	// Get workflows available for key creation (endpoint-enabled, non-public, and no existing key)
	const availableWorkflows = useMemo(() => {
		if (!workflowsData?.workflows || !keys) return [];

		// Get set of workflow IDs that already have keys
		const workflowsWithKeys = new Set(
			keys.filter((k) => k.workflow_id).map((k) => k.workflow_id),
		);

		return workflowsData.workflows
			.filter(
				(w: WorkflowMetadata) =>
					w.endpoint_enabled &&
					!w.public_endpoint &&
					!workflowsWithKeys.has(w.id ?? ""),
			)
			.sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
	}, [workflowsData, keys]);

	// Set of workflow IDs for orphan checking
	const workflowIdSet = useMemo(() => {
		return new Set(workflows.map((w) => w.id));
	}, [workflows]);

	// Sort and categorize keys
	const sortedKeys = useMemo(() => {
		if (!keys) return [];

		return [...keys].sort((a, b) => {
			// Global keys first
			const aIsGlobal = !a.workflow_id;
			const bIsGlobal = !b.workflow_id;
			if (aIsGlobal && !bIsGlobal) return -1;
			if (!aIsGlobal && bIsGlobal) return 1;

			// Then check for orphaned keys (workflow-specific but workflow doesn't exist)
			const aIsOrphaned =
				a.workflow_id && !workflowIdSet.has(a.workflow_id);
			const bIsOrphaned =
				b.workflow_id && !workflowIdSet.has(b.workflow_id);
			if (!aIsOrphaned && bIsOrphaned) return -1;
			if (aIsOrphaned && !bIsOrphaned) return 1;

			// Otherwise sort by creation date (newest first)
			return (
				new Date(b.created_at || 0).getTime() -
				new Date(a.created_at || 0).getTime()
			);
		});
	}, [keys, workflowIdSet]);

	// Check if a key is orphaned
	const isOrphaned = (key: WorkflowKeyResponse) => {
		if (!key.workflow_id) return false;
		return !workflowIdSet.has(key.workflow_id);
	};

	// Check if form is valid
	const isFormValid = useMemo(() => {
		if (!formData.description.trim()) return false;
		if (!formData.isGlobal && !formData.workflowId) return false;
		return true;
	}, [formData]);

	const handleCreate = () => {
		setFormData({
			workflowId: "",
			expiresInDays: "90",
			description: "",
			isGlobal: false,
		});
		setIsCreateDialogOpen(true);
	};

	const handleRevoke = (key: WorkflowKeyResponse) => {
		setSelectedKey(key);
		setIsRevokeDialogOpen(true);
	};

	const handleSubmitCreate = async (e: React.FormEvent) => {
		e.preventDefault();

		if (!isFormValid) return;

		try {
			const result = await createMutation.mutateAsync({
				workflow_id: formData.isGlobal
					? undefined
					: formData.workflowId,
				expires_in_days: formData.expiresInDays
					? parseInt(formData.expiresInDays)
					: undefined,
				description: formData.description,
				disable_global_key: false,
			});

			setIsCreateDialogOpen(false);
			setCreatedKey(result);
			setIsKeyDisplayDialogOpen(true);
		} catch {
			// Error toast is handled by the hook
		}
	};

	const handleConfirmRevoke = async () => {
		if (!selectedKey?.id) return;

		await revokeMutation.mutateAsync(selectedKey.id);
		setIsRevokeDialogOpen(false);
		setSelectedKey(undefined);
	};

	const handleCopyKey = () => {
		if (!createdKey?.raw_key) return;

		navigator.clipboard.writeText(createdKey.raw_key);
		setCopied(true);
		toast.success("API key copied to clipboard");
		setTimeout(() => setCopied(false), 2000);
	};

	const formatDate = (dateString?: string | null) => {
		if (!dateString) return "-";
		const date = new Date(dateString);
		return date.toLocaleDateString("en-US", {
			month: "short",
			day: "numeric",
			year: "numeric",
			hour: "2-digit",
			minute: "2-digit",
		});
	};

	const formatShortDate = (dateString?: string | null) => {
		if (!dateString) return null;
		const date = new Date(dateString);
		return date.toLocaleDateString("en-US", {
			month: "short",
			day: "numeric",
		});
	};

	const formatRelativeDate = (dateString?: string | null) => {
		if (!dateString) return null;
		const date = new Date(dateString);
		const now = new Date();
		const diffMs = now.getTime() - date.getTime();
		const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

		if (diffDays === 0) return "Today";
		if (diffDays === 1) return "Yesterday";
		if (diffDays < 7) return `${diffDays} days ago`;
		if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
		return formatShortDate(dateString);
	};

	const isExpired = (expiresAt?: string | null) => {
		if (!expiresAt) return false;
		return new Date(expiresAt) < new Date();
	};

	return (
		<div className="flex flex-col h-full">
			<Card className="flex-1 flex flex-col overflow-hidden">
				<CardHeader className="flex-shrink-0">
					<div className="flex items-center justify-between">
						<div>
							<CardTitle>Workflow Keys</CardTitle>
							<CardDescription>
								Generate API keys for external systems to
								trigger workflows. Global keys work with all
								workflows, workflow-specific keys are scoped to
								individual workflows.
							</CardDescription>
						</div>
						<div className="flex items-center gap-2">
							<Button
								variant="outline"
								size="icon"
								onClick={handleCreate}
								title="Create API Key"
							>
								<Plus className="h-4 w-4" />
							</Button>
							<Button
								variant="outline"
								size="icon"
								onClick={() => refetch()}
								disabled={isFetching}
								title="Refresh"
							>
								<RefreshCw
									className={`h-4 w-4 ${
										isFetching ? "animate-spin" : ""
									}`}
								/>
							</Button>
						</div>
					</div>
				</CardHeader>
				<CardContent className="flex-1 overflow-hidden flex flex-col">
					{isFetching ? (
						<div className="flex items-center justify-center py-12">
							<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
						</div>
					) : sortedKeys && sortedKeys.length > 0 ? (
						<div className="flex-1 overflow-auto space-y-3">
							{sortedKeys.map((key) => {
								const orphaned = isOrphaned(key);
								const expired = isExpired(key.expires_at);

								return (
									<div
										key={key.id}
										className={`border rounded-lg p-4 border-l-4 ${
											orphaned || expired
												? "border-destructive/50 border-l-destructive bg-destructive/5"
												: "border-border bg-muted/40 border-l-primary/60"
										}`}
									>
										{/* Top row: Scope badge + description + delete */}
										<div className="flex items-start justify-between gap-3">
											<div className="flex items-center gap-3 min-w-0 flex-1">
												{!key.workflow_name ? (
													<Badge
														variant="default"
														className="text-xs font-semibold shrink-0"
													>
														<Globe className="mr-1 h-3 w-3" />
														Global
													</Badge>
												) : orphaned ? (
													<Badge
														variant="destructive"
														className="font-mono text-xs shrink-0"
													>
														<AlertTriangle className="mr-1 h-3 w-3" />
														{key.workflow_name}
													</Badge>
												) : (
													<Badge
														variant="outline"
														className="font-mono text-xs shrink-0"
													>
														<WorkflowIcon className="mr-1 h-3 w-3" />
														{key.workflow_name}
													</Badge>
												)}
												<span className="text-sm truncate">
													{key.description || (
														<span className="text-muted-foreground italic">
															No description
														</span>
													)}
												</span>
											</div>
											<Button
												variant="ghost"
												size="icon"
												className="shrink-0 h-8 w-8"
												onClick={() =>
													handleRevoke(key)
												}
												title="Revoke key"
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										</div>

										{/* Warning for orphaned keys */}
										{orphaned && (
											<p className="text-xs text-destructive mt-2">
												Warning: Workflow no longer
												exists
											</p>
										)}

										{/* Bottom row: Key + metadata */}
										<div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
											<code className="text-sm font-mono text-muted-foreground bg-muted px-2 py-1 rounded">
												{key.masked_key}
											</code>
											<div className="flex items-center gap-4 text-xs text-muted-foreground">
												<span>
													Created{" "}
													{formatRelativeDate(
														key.created_at,
													)}
												</span>
												{key.last_used_at && (
													<span>
														Used{" "}
														{formatRelativeDate(
															key.last_used_at,
														)}
													</span>
												)}
												{key.expires_at ? (
													expired ? (
														<Badge
															variant="destructive"
															className="text-xs"
														>
															Expired
														</Badge>
													) : (
														<span>
															Expires{" "}
															{formatShortDate(
																key.expires_at,
															)}
														</span>
													)
												) : (
													<span className="text-muted-foreground/60">
														Never expires
													</span>
												)}
											</div>
										</div>
									</div>
								);
							})}
						</div>
					) : (
						<div className="flex flex-col items-center justify-center py-12 text-center">
							<Key className="h-12 w-12 text-muted-foreground" />
							<h3 className="mt-4 text-lg font-semibold">
								No API keys found
							</h3>
							<p className="mt-2 text-sm text-muted-foreground">
								Create your first API key to enable HTTP access
								to workflows
							</p>
							<Button
								variant="outline"
								size="icon"
								onClick={handleCreate}
								title="Create API Key"
								className="mt-4"
							>
								<Plus className="h-4 w-4" />
							</Button>
						</div>
					)}
				</CardContent>
			</Card>

			{/* Create Dialog */}
			<Dialog
				open={isCreateDialogOpen}
				onOpenChange={setIsCreateDialogOpen}
			>
				<DialogContent className="sm:max-w-xl">
					<form onSubmit={handleSubmitCreate}>
						<DialogHeader>
							<DialogTitle>Create Workflow API Key</DialogTitle>
							<DialogDescription>
								Generate a new API key for external systems to
								trigger workflows. The key will only be shown
								once - make sure to copy it.
							</DialogDescription>
						</DialogHeader>
						<div className="space-y-4 py-4">
							{/* Description Field - Required */}
							<div className="space-y-2">
								<Label htmlFor="description">
									Description{" "}
									<span className="text-destructive">*</span>
								</Label>
								<Input
									id="description"
									value={formData.description}
									onChange={(e) =>
										setFormData({
											...formData,
											description: e.target.value,
										})
									}
									placeholder="Production API Key for CRM"
									maxLength={32}
									required
								/>
								<p className="text-xs text-muted-foreground">
									Brief description (max 32 characters)
								</p>
							</div>

							{/* Global Key Toggle */}
							<div className="flex items-center space-x-2">
								<Checkbox
									id="isGlobal"
									checked={formData.isGlobal}
									onCheckedChange={(checked) =>
										setFormData({
											...formData,
											isGlobal: checked === true,
											workflowId:
												checked === true
													? ""
													: formData.workflowId,
										})
									}
								/>
								<div className="flex flex-col">
									<Label
										htmlFor="isGlobal"
										className="cursor-pointer font-medium"
									>
										Global Key
									</Label>
									<p className="text-xs text-muted-foreground">
										Allow this key to execute any workflow
									</p>
								</div>
							</div>

							{/* Workflow Selector - Conditionally Required */}
							{!formData.isGlobal && (
								<div className="space-y-2">
									<Label htmlFor="workflowId">
										Workflow{" "}
										<span className="text-destructive">
											*
										</span>
									</Label>
									<Combobox
										id="workflowId"
										value={formData.workflowId}
										onValueChange={(value) =>
											setFormData({
												...formData,
												workflowId: value,
											})
										}
										options={availableWorkflows.map(
											(wf: WorkflowMetadata) => ({
												value: wf.id,
												label: wf.name,
											}),
										)}
										placeholder="Select a workflow..."
										searchPlaceholder="Search workflows..."
										emptyText="No workflows found."
									/>
									<p className="text-xs text-muted-foreground">
										Restrict this key to a specific workflow
									</p>
								</div>
							)}

							{/* Expiration Field - Optional */}
							<div className="space-y-2">
								<Label htmlFor="expiresInDays">
									Expiration (days)
								</Label>
								<Combobox
									id="expiresInDays"
									value={formData.expiresInDays}
									onValueChange={(value) =>
										setFormData({
											...formData,
											expiresInDays: value,
										})
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
									How long until the key expires (optional)
								</p>
							</div>
						</div>
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => setIsCreateDialogOpen(false)}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								disabled={
									createMutation.isPending || !isFormValid
								}
							>
								{createMutation.isPending
									? "Creating..."
									: "Create API Key"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			{/* Key Display Dialog (One-Time Display) */}
			<Dialog
				open={isKeyDisplayDialogOpen}
				onOpenChange={setIsKeyDisplayDialogOpen}
			>
				<DialogContent className="max-w-2xl">
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2">
							<Check className="h-5 w-5 text-green-500" />
							API Key Created Successfully
						</DialogTitle>
						<DialogDescription>
							This is the only time you'll be able to view this
							key. Copy it now and store it securely.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-4 py-4">
						<Alert variant="destructive">
							<AlertTriangle className="h-4 w-4" />
							<AlertDescription>
								<strong>Important:</strong> This key will not be
								shown again. Make sure to copy it before closing
								this dialog.
							</AlertDescription>
						</Alert>

						<div className="space-y-2">
							<Label>Your API Key</Label>
							<div className="flex gap-2">
								<Input
									value={createdKey?.raw_key || ""}
									readOnly
									className="font-mono text-sm"
								/>
								<Button
									type="button"
									variant="outline"
									size="icon"
									onClick={handleCopyKey}
									className="flex-shrink-0"
								>
									{copied ? (
										<Check className="h-4 w-4" />
									) : (
										<Copy className="h-4 w-4" />
									)}
								</Button>
							</div>
						</div>

						<div className="space-y-2">
							<Label>Usage Example</Label>
							<div className="bg-muted p-3 rounded-md ring-1 ring-foreground/5">
								<pre className="text-xs overflow-x-auto">
									<code>{`curl -X POST ${
										window.location.protocol
									}//${window.location.host}/api/endpoints/${
										createdKey?.workflow_id ||
										"{workflowId}"
									} \\
  -H "X-Bifrost-Key: ${createdKey?.raw_key}" \\
  -H "Content-Type: application/json" \\
  -d '{"input": "your data"}'`}</code>
								</pre>
							</div>
						</div>

						{createdKey?.description && (
							<div className="space-y-2">
								<Label>Description</Label>
								<p className="text-sm text-muted-foreground">
									{createdKey.description}
								</p>
							</div>
						)}

						<div className="grid grid-cols-2 gap-4 text-sm">
							<div>
								<Label className="text-xs text-muted-foreground">
									Scope
								</Label>
								<p className="mt-1">
									{createdKey?.workflow_name ? (
										<Badge
											variant="outline"
											className="font-mono"
										>
											{createdKey.workflow_name}
										</Badge>
									) : (
										<Badge variant="default">Global</Badge>
									)}
								</p>
							</div>
							<div>
								<Label className="text-xs text-muted-foreground">
									Expires
								</Label>
								<p className="mt-1">
									{createdKey?.expires_at
										? formatDate(createdKey.expires_at)
										: "Never"}
								</p>
							</div>
						</div>
					</div>
					<DialogFooter>
						<Button
							type="button"
							onClick={() => {
								setIsKeyDisplayDialogOpen(false);
								setCreatedKey(undefined);
								setCopied(false);
							}}
						>
							I've Copied the Key
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Revoke Confirmation Dialog */}
			<AlertDialog
				open={isRevokeDialogOpen}
				onOpenChange={setIsRevokeDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Revoke API Key?</AlertDialogTitle>
						<AlertDialogDescription>
							This will immediately revoke the API key ending in{" "}
							<strong className="font-mono">
								{selectedKey?.masked_key}
							</strong>
							. Any systems using this key will no longer be able
							to execute workflows. This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmRevoke}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{revokeMutation.isPending
								? "Revoking..."
								: "Revoke Key"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
