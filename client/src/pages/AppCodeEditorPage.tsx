/**
 * App Code Editor Page
 *
 * Editor for creating and modifying code-based App Builder applications.
 * Uses the file-based routing pattern with code-first development.
 */

import { useState, useRef, useEffect } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { parseSolutionFrom } from "@/lib/solution-back-nav";
import { ArrowLeft, Upload, Settings, Loader2, Link } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { getErrorMessage } from "@/lib/api-error";
import { ListLoadError } from "@/components/layout/ListLoadError";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { AppInfoDialog } from "@/components/app-builder/AppInfoDialog";
import { EmbedSettingsDialog } from "@/components/app-builder/EmbedSettingsDialog";
import { toast } from "sonner";
import { AppCodeEditorLayout } from "@/components/app-code-editor/AppCodeEditorLayout";
import { SolutionManagedBanner } from "@/components/solutions/SolutionManagedBanner";
import {
	useApplication,
	useCreateApplication,
	usePublishApplication,
} from "@/hooks/useApplications";
import { useAuth } from "@/contexts/AuthContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";

export function AppCodeEditorPage() {
	const navigate = useNavigate();
	const { applicationId: slugParam } = useParams();
	const { search } = useLocation();
	const fromSolution = parseSolutionFrom(search);
	const backTo = fromSolution ? `/solutions/${fromSolution}` : "/apps";
	const isEditing = !!slugParam;
	const { user, isPlatformAdmin } = useAuth();

	// Default organization_id
	const defaultOrgId = isPlatformAdmin
		? null
		: (user?.organizationId ?? null);

	// Fetch existing application metadata
	const {
		data: existingApp,
		isLoading: isLoadingApp,
		isFetching: isFetchingApp,
		refetch: refetchApp,
	} = useApplication(isEditing ? slugParam : undefined);

	// Mutations
	const createApplication = useCreateApplication({ errorToast: false });
	const publishApplication = usePublishApplication({ errorToast: false });

	// Form state for new apps (only used when !isEditing)
	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [slug, setSlug] = useState("");
	const [slugEdited, setSlugEdited] = useState(false);
	const [createError, setCreateError] = useState<string | null>(null);
	const createErrorRef = useRef<HTMLParagraphElement>(null);
	useEffect(() => {
		if (createError) {
			createErrorRef.current?.focus();
			createErrorRef.current?.scrollIntoView({ block: "center" });
		}
	}, [createError]);
	const [organizationId, setOrganizationId] = useState<string | null>(
		defaultOrgId,
	);

	// Dialog state
	const [isPublishDialogOpen, setIsPublishDialogOpen] = useState(false);
	const [publishMessage, setPublishMessage] = useState("");
	const [publishError, setPublishError] = useState<string | null>(null);
	const [isSettingsOpen, setIsSettingsOpen] = useState(false);
	const [isEmbedOpen, setIsEmbedOpen] = useState(false);

	// Solution-managed apps are read-only on the platform (deploy is the only
	// writer). The API rejects edits/publish regardless; this drives the UI
	// affordance (banner + hidden Publish).
	const isManaged = !!existingApp?.is_solution_managed;

	// For existing apps, we skip the creation form and go straight to the editor
	// For new apps, we show the creation form first
	const appCreated = isEditing ? !!existingApp : false;

	// Auto-generate slug from name (only for new apps)
	const handleNameChange = (newName: string) => {
		setName(newName);
		// Auto-generate slug if it hasn't been manually edited
		if (!isEditing && !slugEdited) {
			const generated = newName
				.toLowerCase()
				.replace(/[^a-z0-9]+/g, "-")
				.replace(/^-|-$/g, "");
			setSlug(generated);
		}
	};

	// Create a new code-based application
	const handleCreate = async () => {
		if (isEditing || createApplication.isPending) return;
		setCreateError(null);
		if (!name.trim()) {
			toast.error("Please enter an application name");
			return;
		}

		try {
			const result = await createApplication.mutateAsync({
				body: {
					name,
					description: description || null,
					slug,
					organization_id: organizationId,
					access_level: "authenticated",
					app_model: "standalone_v2",
				},
			});

			// Navigate to edit the new app
			navigate(`/apps/${result.slug}/edit`, { replace: true });
		} catch (error) {
			setCreateError(
				getErrorMessage(error, "Failed to create application"),
			);
		}
	};

	const handlePublish = async () => {
		if (!existingApp?.id || publishApplication.isPending) return;
		setPublishError(null);

		try {
			await publishApplication.mutateAsync({
				params: { path: { app_id: existingApp.id } },
				body: { message: publishMessage || null },
			});
			setIsPublishDialogOpen(false);
			setPublishMessage("");
			publishApplication.reset();
		} catch (error) {
			setPublishError(
				getErrorMessage(
					error,
					"Could not start publishing. Try again.",
				),
			);
		}
	};

	// Loading state
	if (isEditing && isLoadingApp) {
		return (
			<div className="space-y-6">
				<div className="flex min-w-0 flex-1 items-center gap-2">
					<Skeleton className="h-10 w-10" />
					<Skeleton className="h-8 w-64" />
				</div>
				<Skeleton className="h-[600px] w-full" />
			</div>
		);
	}

	if (isEditing && !existingApp) {
		return (
			<div className="space-y-6">
				<div className="flex items-center gap-3">
					<Button
						variant="ghost"
						size="icon-lg"
						aria-label={
							fromSolution ? "Back to Solution" : "Back to Apps"
						}
						onClick={() => navigate(backTo)}
					>
						<ArrowLeft className="h-5 w-5" />
					</Button>
					<h1 className="font-display text-2xl font-semibold">
						Application unavailable
					</h1>
				</div>
				<ListLoadError
					resource="this application"
					hasCachedData={false}
					isRetrying={isFetchingApp}
					onRetry={() => void refetchApp()}
				/>
			</div>
		);
	}

	// Show creation form for new apps
	if (!appCreated) {
		return (
			<div className="flex h-full min-h-0 flex-col">
				{/* Header */}
				<div className="flex items-center justify-between pb-4">
					<div className="flex min-w-0 flex-1 items-center gap-2">
						<Button
							variant="ghost"
							size="icon-lg"
							onClick={() => navigate("/apps")}
							className="shrink-0"
							aria-label={
								fromSolution
									? "Back to Solution"
									: "Back to Apps"
							}
						>
							<ArrowLeft className="h-5 w-5" />
						</Button>
						<h1 className="text-xl font-semibold">
							New Code Application
						</h1>
					</div>
				</div>

				{/* Creation Form */}
				<div className="mx-auto flex w-full max-w-xl flex-1 min-h-0 flex-col gap-6 overflow-auto px-3 py-6 sm:px-4">
					<div className="space-y-2">
						<Label htmlFor="name">Name</Label>
						<Input
							id="name"
							value={name}
							onChange={(e) => handleNameChange(e.target.value)}
							placeholder="My Code Application"
							autoFocus
						/>
					</div>

					<div className="space-y-2">
						<Label htmlFor="description">Description</Label>
						<Textarea
							id="description"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							placeholder="A brief description of your application..."
							rows={3}
						/>
					</div>

					<div className="space-y-2">
						<Label htmlFor="slug">URL Slug</Label>
						<Input
							id="slug"
							value={slug}
							onChange={(e) => {
								setSlugEdited(true);
								setSlug(e.target.value);
							}}
							placeholder="my-code-application"
						/>
						<p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
							Your app will be accessible at /apps/{slug || "..."}
						</p>
					</div>

					{isPlatformAdmin && (
						<div className="space-y-2">
							<Label>Organization Scope</Label>
							<OrganizationSelect
								value={organizationId}
								onChange={(val) =>
									setOrganizationId(val ?? null)
								}
								showGlobal={true}
							/>
						</div>
					)}

					{createError && (
						<p
							role="alert"
							ref={createErrorRef}
							tabIndex={-1}
							className="rounded-[var(--bf-radius-surface)] border border-destructive/30 p-3 text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{createError}
						</p>
					)}
					<div className="flex flex-wrap gap-3 pt-4">
						<Button
							variant="outline"
							onClick={() => navigate("/apps")}
							size="lg"
						>
							Cancel
						</Button>
						<Button
							onClick={handleCreate}
							disabled={
								createApplication.isPending || !name.trim()
							}
							size="lg"
						>
							{createApplication.isPending ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
									Creating...
								</>
							) : createError ? (
								"Retry create"
							) : (
								"Create Application"
							)}
						</Button>
					</div>
				</div>
			</div>
		);
	}

	// Show the code editor for existing apps
	const isPublishing = publishApplication.isPending;
	const hasDraft = existingApp?.has_unpublished_changes;

	return (
		<div className="h-full min-h-0 flex flex-col -mx-4 sm:-mx-6 lg:-mx-8 -mb-4 sm:-mb-6 lg:-mb-8">
			{/* Header */}
			<div className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-3 py-2 border-b bg-background sm:px-4">
				<div className="flex min-w-0 basis-full items-center gap-2 sm:basis-auto sm:flex-1">
					<Button
						variant="ghost"
						size="icon-lg"
						onClick={() => navigate(backTo)}
						className="shrink-0"
						aria-label={
							fromSolution ? "Back to Solution" : "Back to Apps"
						}
					>
						<ArrowLeft className="h-5 w-5" />
					</Button>
					<div className="flex min-w-0 items-center gap-3">
						<h1 className="truncate font-display text-lg font-semibold">
							{existingApp?.name || "Edit Application"}
						</h1>
					</div>
				</div>

				<div className="ml-auto flex items-center gap-2">
					{/* Embed — hidden for managed apps (embed secrets are mutations) */}
					{isEditing && existingApp && !isManaged && (
						<>
							<Button
								variant="ghost"
								size="icon-lg"
								onClick={() => setIsEmbedOpen(true)}
								title="Embed settings"
								aria-label="Embed settings"
							>
								<Link className="h-4 w-4" />
							</Button>
							<EmbedSettingsDialog
								appId={existingApp.id}
								appSlug={existingApp.slug}
								open={isEmbedOpen}
								onOpenChange={setIsEmbedOpen}
							/>
						</>
					)}

					{/* Settings — hidden for solution-managed apps: the dialog
					    exposes Save/Delete/Replace/logo mutations the API rejects. */}
					{!isManaged && (
						<>
							<Button
								variant="ghost"
								size="icon-lg"
								onClick={() => setIsSettingsOpen(true)}
								title="Settings"
								aria-label="Settings"
							>
								<Settings className="h-4 w-4" />
							</Button>
							<AppInfoDialog
								appSlug={existingApp?.slug}
								open={isSettingsOpen}
								onOpenChange={setIsSettingsOpen}
							/>
						</>
					)}

					{/* Publish — hidden for solution-managed apps (deploy is the
					    only writer; the API rejects publish regardless). */}
					{hasDraft && !isManaged && (
						<Button
							size="lg"
							onClick={() => setIsPublishDialogOpen(true)}
							disabled={isPublishing}
						>
							<Upload className="mr-2 h-4 w-4" />
							{isPublishing ? "Publishing..." : "Publish"}
						</Button>
					)}
				</div>
			</div>

			{/* Solution-managed read-only affordance */}
			{isManaged && (
				<div className="px-4 pt-3">
					<SolutionManagedBanner entityLabel="app" />
				</div>
			)}

			{/* Code Editor */}
			<div className="flex-1 min-h-0">
				{existingApp?.id && (
					<AppCodeEditorLayout
						key={existingApp.id}
						appId={existingApp.id}
						solutionId={existingApp.solution_id}
						appName={existingApp.name}
						appSlug={existingApp.slug}
						readOnly={isManaged}
					/>
				)}
			</div>

			{/* Publish Dialog */}
			<Dialog
				open={isPublishDialogOpen}
				onOpenChange={(open) => {
					if (!isPublishing) {
						setIsPublishDialogOpen(open);
						if (!open) {
							publishApplication.reset();
							setPublishError(null);
						}
					}
				}}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Publish Application</DialogTitle>
						<DialogDescription>
							Publish the current draft. The new version will go
							live when publishing finishes. Follow its progress
							in notifications.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="publish-message">
								Publish Message (optional)
							</Label>
							<Textarea
								id="publish-message"
								value={publishMessage}
								onChange={(e) =>
									setPublishMessage(e.target.value)
								}
								placeholder="What changed in this version?"
								rows={3}
								disabled={isPublishing}
							/>
						</div>
					</div>
					{publishError && (
						<Alert variant="destructive">
							<AlertDescription className="[overflow-wrap:anywhere]">
								{publishError}
							</AlertDescription>
						</Alert>
					)}
					<DialogFooter>
						<Button
							variant="outline"
							className="min-h-11"
							onClick={() => {
								setIsPublishDialogOpen(false);
								setPublishError(null);
								publishApplication.reset();
							}}
							disabled={isPublishing}
						>
							Cancel
						</Button>
						<Button
							className="min-h-11"
							onClick={handlePublish}
							disabled={isPublishing}
						>
							{isPublishing ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
									Publishing...
								</>
							) : (
								"Publish"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
