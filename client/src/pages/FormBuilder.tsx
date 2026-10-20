import { useState, useMemo, useRef, useEffect } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { parseSolutionFrom } from "@/lib/solution-back-nav";
import { ArrowLeft, Save, Eye, Pencil, Info, Play, Share2 } from "lucide-react";
import { SolutionManagedBanner } from "@/components/solutions/SolutionManagedBanner";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { ContextViewer } from "@/components/ui/context-viewer";
import {
	useForm as useFormQuery,
	useCreateForm,
	useUpdateForm,
	executeFormStartup,
} from "@/hooks/useForms";
import { assignRolesToForm } from "@/hooks/useRoles";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { FormInfoDialog } from "@/components/forms/FormInfoDialog";
import { FormLogoEditor } from "@/components/forms/FormLogoEditor";
import { FormShareDialog } from "@/components/forms/FormShareDialog";
import type { FormInfoValues } from "@/components/forms/FormInfoDialog";
import { FieldsPanelDnD } from "@/components/forms/FieldsPanelDnD";
import { FormPreview } from "@/components/forms/FormPreview";
import { WorkflowParametersForm } from "@/components/workflows/WorkflowParametersForm";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";
type FormCreate = components["schemas"]["FormCreate"];
type FormUpdate = components["schemas"]["FormUpdate"];
type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/api-error";

export function FormBuilder() {
	const navigate = useNavigate();
	const [savePartial, setSavePartial] = useState(false);
	const [saveError, setSaveError] = useState<string | null>(null);
	const [isSaving, setIsSaving] = useState(false);
	const saveBusy = useRef(false);
	// Keep the created identity if the subsequent role assignment needs a retry.
	const createdFormId = useRef<string | null>(null);
	const saveErrorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (!saveError) return;
		saveErrorRef.current?.focus();
		saveErrorRef.current?.scrollIntoView({ block: "center" });
	}, [saveError]);
	const { formId } = useParams();
	const { search } = useLocation();
	const fromSolution = parseSolutionFrom(search);
	const backTo = fromSolution ? `/solutions/${fromSolution}` : "/forms";
	const isEditing = !!formId;
	const { scope } = useOrgScope();
	const { user } = useAuth();
	const authorization = useAuthorization();

	const {
		data: existingForm,
		isLoading: formLoading,
		error: formLoadError,
		isFetching: formFetching,
		refetch: refetchForm,
	} = useFormQuery(formId);
	const createForm = useCreateForm({
		errorToast: false,
		successToast: false,
	});
	const updateForm = useUpdateForm({
		errorToast: false,
		successToast: false,
	});
	const {
		data: workflowsMetadata,
		isError: workflowsError,
		isLoading: workflowsLoading,
		isFetching: workflowsFetching,
		refetch: refetchWorkflows,
	} = useWorkflowsMetadata();

	// Solution-managed forms are read-only on the platform (criterion 6).
	const isSolutionManaged = existingForm?.is_solution_managed ?? false;

	const defaultOrgId = user?.organizationId ?? null;

	// Lightweight state for form metadata (set when dialog saves, or synced from existingForm)
	const [formInfo, setFormInfo] = useState<FormInfoValues | null>(null);

	// Fields state for the drag-and-drop builder (separate from dialog metadata)
	const [fields, setFields] = useState<FormField[]>([]);

	// UI state
	const [isInfoDialogOpen, setIsInfoDialogOpen] = useState(() => !isEditing);
	const [isShareDialogOpen, setIsShareDialogOpen] = useState(false);
	const [isContextDialogOpen, setIsContextDialogOpen] = useState(false);
	const [workflowResultsDialogOpen, setWorkflowResultsDialogOpen] =
		useState(false);
	const [workflowParamsDialogOpen, setWorkflowParamsDialogOpen] =
		useState(false);
	const [workflowResults, setWorkflowResults] = useState<Record<
		string,
		unknown
	> | null>(null);
	const [isTestingWorkflow, setIsTestingWorkflow] = useState(false);
	const testBusy = useRef(false);
	const [testError, setTestError] = useState<string | null>(null);

	// Sync fields and formInfo when existingForm loads. Adjusting state
	// during render with a "previous existingForm" sentinel is the React-
	// recommended idiom for prop-driven resets and avoids an extra effect
	// render cycle.
	const [prevExistingFormId, setPrevExistingFormId] = useState<string | null>(
		null,
	);
	const currentExistingFormId = existingForm?.id ?? null;
	if (existingForm && prevExistingFormId !== currentExistingFormId) {
		setPrevExistingFormId(currentExistingFormId);
		// Sync fields from form_schema
		if (
			existingForm.form_schema &&
			typeof existingForm.form_schema === "object" &&
			"fields" in existingForm.form_schema
		) {
			const schema = existingForm.form_schema as {
				fields: unknown[];
			};
			setFields(schema.fields as FormField[]);
		}

		// Sync formInfo from existingForm (only if not already overridden by dialog save)
		if (!formInfo) {
			setFormInfo({
				name: existingForm.name || "",
				description: existingForm.description || "",
				workflow_id: existingForm.workflow_id || "",
				launch_workflow_id: existingForm.launch_workflow_id || "",
				default_launch_params:
					(existingForm.default_launch_params as Record<
						string,
						unknown
					>) || {},
				access_level:
					(existingForm.access_level as
						"authenticated" | "everyone" | "role_based") ||
					"role_based",
				role_ids: existingForm.role_ids ?? [],
				organization_id: existingForm.organization_id ?? null,
			});
		}
	}

	// Derive display values from formInfo (dialog-saved) or existingForm (server data)
	const formName = formInfo?.name ?? existingForm?.name ?? "";
	const formDescription =
		formInfo?.description ?? existingForm?.description ?? "";
	const linkedWorkflow =
		formInfo?.workflow_id ?? existingForm?.workflow_id ?? "";
	const launchWorkflowId =
		formInfo?.launch_workflow_id ?? existingForm?.launch_workflow_id ?? "";
	const defaultLaunchParams =
		formInfo?.default_launch_params ||
		(existingForm?.default_launch_params as Record<string, unknown>) ||
		{};
	const accessLevel =
		formInfo?.access_level ||
		(existingForm?.access_level as
			"authenticated" | "everyone" | "role_based") ||
		"role_based";
	const selectedRoleIds = formInfo?.role_ids || [];
	const organizationId = formInfo
		? formInfo.organization_id
		: existingForm
			? (existingForm.organization_id ?? null)
			: defaultOrgId;
	const isGlobal = organizationId === null;
	const canManageForm =
		authorization.canAt("forms.readwrite", orgTarget(organizationId)) &&
		(!existingForm ||
			authorization.canAt(
				"forms.readwrite",
				orgTarget(existingForm.organization_id),
			));

	const handleInfoSave = (info: FormInfoValues) => {
		setFormInfo(info);
	};

	const handleSave = async () => {
		if (saveBusy.current || !canManageForm) return;
		saveBusy.current = true;
		setIsSaving(true);
		setSaveError(null);
		setSavePartial(false);
		let formPersisted = false;
		try {
			// Auto-generate allowedQueryParams from fields that have allow_as_query_param enabled
			const autoGeneratedParams = fields
				.filter((field) => field.allow_as_query_param === true)
				.map((field) => field.name);

			const savedFormId = formId || createdFormId.current;
			if (savedFormId) {
				const updateRequest: FormUpdate = {
					organization_id: organizationId,
					name: formName,
					description: formDescription || null,
					workflow_id: linkedWorkflow || null,
					form_schema: { fields },
					is_active: true,
					access_level: accessLevel,
					launch_workflow_id: launchWorkflowId || null,
					allowed_query_params:
						autoGeneratedParams.length > 0
							? autoGeneratedParams
							: null,
					default_launch_params:
						Object.keys(defaultLaunchParams).length > 0
							? defaultLaunchParams
							: null,
					clear_roles: false,
					role_ids: formId
						? accessLevel === "role_based"
							? selectedRoleIds
							: []
						: undefined,
				};
				await updateForm.mutateAsync({
					params: { path: { form_id: savedFormId } },
					body: updateRequest,
				});

				formPersisted = true;
				if (!formId && accessLevel === "role_based") {
					await assignRolesToForm(savedFormId, selectedRoleIds);
				}
			} else {
				const createRequest: FormCreate = {
					name: formName,
					description: formDescription || null,
					confirmation_markdown: "## Form submitted\n\nThank you!",
					workflow_id: linkedWorkflow || null,
					form_schema: { fields },
					access_level: accessLevel,
					organization_id: organizationId,
					launch_workflow_id: launchWorkflowId || null,
					allowed_query_params:
						autoGeneratedParams.length > 0
							? autoGeneratedParams
							: null,
					default_launch_params:
						Object.keys(defaultLaunchParams).length > 0
							? defaultLaunchParams
							: null,
				};
				const createdForm = await createForm.mutateAsync({
					body: createRequest,
				});
				createdFormId.current = createdForm?.id ?? null;
				formPersisted = true;

				if (
					accessLevel === "role_based" &&
					createdForm?.id &&
					selectedRoleIds.length > 0
				) {
					await assignRolesToForm(createdForm.id, selectedRoleIds);
				}
			}

			toast.success("Form saved");
			navigate(backTo);
		} catch (error: unknown) {
			if (formPersisted) {
				setSavePartial(true);
				setSaveError(
					"The form is saved, but its access roles could not be updated. Retry to finish saving.",
				);
				return;
			}
			const errorResponse = error as {
				response?: {
					data?: {
						message?: string;
						details?: { errors?: { loc: string[]; msg: string }[] };
					};
				};
			} & Error;
			const errorMessage =
				errorResponse?.response?.data?.message ||
				getErrorMessage(error, "Failed to save form");
			const errorDetails = errorResponse?.response?.data?.details;

			if (errorDetails?.errors) {
				const validationErrors = errorDetails.errors
					.map(
						(err: { loc: string[]; msg: string }) =>
							`${err.loc.join(".")}: ${err.msg}`,
					)
					.join("\n");
				setSaveError(`Validation error\n${validationErrors}`);
			} else {
				setSaveError(errorMessage);
			}
		} finally {
			saveBusy.current = false;
			setIsSaving(false);
		}
	};

	// Validate that all required workflow parameters have corresponding form fields
	const validateRequiredParameters = (): {
		valid: boolean;
		missingParams: string[];
	} => {
		if (!linkedWorkflow || !workflowsMetadata?.workflows) {
			return { valid: true, missingParams: [] };
		}

		const workflow = (
			workflowsMetadata.workflows as WorkflowMetadata[]
		).find((w: WorkflowMetadata) => w.id === linkedWorkflow);
		if (!workflow || !workflow.parameters) {
			return { valid: true, missingParams: [] };
		}

		const requiredParams = workflow.parameters
			.filter((param) => param.required)
			.map((param) => param.name);

		const fieldNames = new Set(fields.map((field) => field.name));

		const missingParams = requiredParams.filter(
			(paramName) => !fieldNames.has(paramName),
		);

		return {
			valid: missingParams.length === 0,
			missingParams,
		};
	};

	const validationResult = validateRequiredParameters();
	const isSaveDisabled =
		!canManageForm ||
		!formName ||
		!linkedWorkflow ||
		fields.length === 0 ||
		!validationResult.valid;

	// Handle test launch workflow execution
	const handleTestLaunchWorkflow = async (
		params?: Record<string, unknown>,
	) => {
		if (testBusy.current) return;
		setTestError(null);
		if (!launchWorkflowId) {
			setTestError("No launch workflow configured");
			return;
		}

		if (!formId) {
			setTestError(
				"Please save the form first before testing the launch workflow",
			);
			return;
		}

		try {
			testBusy.current = true;
			setIsTestingWorkflow(true);

			const inputData = { ...defaultLaunchParams, ...params };
			const response = await executeFormStartup(formId, inputData);

			setWorkflowParamsDialogOpen(false);
			setWorkflowResultsDialogOpen(true);
			if (response.result) {
				setWorkflowResults(response.result as Record<string, unknown>);
				setWorkflowResultsDialogOpen(true);
				toast.success("Launch workflow executed successfully");
			} else {
				setWorkflowResults({});
				toast.info("Launch workflow completed with no results");
			}
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Failed to execute launch workflow";
			setTestError(errorMessage);
		} finally {
			testBusy.current = false;
			setIsTestingWorkflow(false);
		}
	};

	// Get workflow metadata for launch workflow
	const launchWorkflow = (
		workflowsMetadata?.workflows as WorkflowMetadata[] | undefined
	)?.find((w: WorkflowMetadata) => w.name === launchWorkflowId);
	const launchWorkflowParameters = launchWorkflow?.parameters || [];

	// Build real context preview based on current user and form state
	const previewContext = useMemo(() => {
		const workflowContext = workflowResults || {
			user_id: user?.id || "user-123",
			user_email: user?.email || "user@example.com",
			organization_id: scope.orgId || null,
		};

		const queryContext: Record<string, string> = {};
		fields
			.filter((field) => field.allow_as_query_param)
			.forEach((field) => {
				queryContext[field.name] =
					`<${field.label?.toLowerCase().replace(/\s+/g, "_") || field.name}>`;
			});

		return {
			workflow: workflowContext,
			query: queryContext,
			field: {},
		};
	}, [user, scope.orgId, fields, workflowResults]);

	if (isEditing && !existingForm) {
		if (formLoading)
			return (
				<div
					role="status"
					aria-label="Loading form designer"
					className="space-y-6"
				>
					<Skeleton className="h-12 w-64 max-w-full" />
					<Skeleton className="h-80 w-full" />
				</div>
			);
		return (
			<Alert variant="destructive">
				<AlertTitle>Form unavailable</AlertTitle>
				<AlertDescription>
					Could not load this form for editing. Retry to check access
					and availability.
				</AlertDescription>
				<Button
					variant="outline"
					className="mt-3 min-h-11"
					disabled={formFetching}
					onClick={() => void refetchForm()}
				>
					Retry form
				</Button>
			</Alert>
		);
	}

	return (
		<div className="mx-auto flex min-h-full w-full max-w-[1400px] flex-col gap-6 lg:h-full lg:min-h-0">
			{formLoadError && existingForm && (
				<Alert variant="destructive">
					<AlertDescription>
						Form details could not refresh. Your edits are
						preserved.
					</AlertDescription>
					<Button
						variant="outline"
						className="mt-3 min-h-11"
						disabled={formFetching}
						onClick={() => void refetchForm()}
					>
						Retry form
					</Button>
				</Alert>
			)}
			{workflowsError && (
				<Alert variant="destructive">
					<AlertDescription>
						Workflow details could not load. Retry before testing
						the launch workflow.
					</AlertDescription>
					<Button
						variant="outline"
						className="mt-3 min-h-11"
						disabled={workflowsFetching}
						onClick={() => void refetchWorkflows()}
					>
						Retry workflows
					</Button>
				</Alert>
			)}

			<div className="flex shrink-0 flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
				<div className="space-y-2">
					<div className="flex min-w-0 items-center gap-3">
						<Button
							variant="ghost"
							size="icon-lg"
							disabled={isSaving}
							onClick={() => navigate(backTo)}
							aria-label={
								fromSolution
									? "Back to Solution"
									: "Back to Forms"
							}
							className="shrink-0"
						>
							<ArrowLeft className="h-5 w-5" />
						</Button>
						{formId && !isSolutionManaged && canManageForm ? (
							<FormLogoEditor
								formId={formId}
								logoUrl={existingForm?.logo_url ?? null}
							/>
						) : null}
						<h1 className="min-w-0 font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
							{formName || (isEditing ? "Edit Form" : "New Form")}
						</h1>
					</div>
					<div className="flex flex-wrap items-center gap-2">
						{linkedWorkflow && (
							<Badge
								variant="outline"
								className="font-mono text-xs"
							>
								{linkedWorkflow}
							</Badge>
						)}
						{isGlobal && (
							<Badge variant="secondary" className="text-xs">
								Global
							</Badge>
						)}
					</div>
					{formDescription && (
						<p className="max-w-2xl text-sm text-muted-foreground">
							{formDescription}
						</p>
					)}
				</div>
				<div className="flex flex-wrap items-center gap-2 lg:justify-end">
					<div className="flex items-center">
						<Button
							variant="outline"
							size="icon-lg"
							disabled={
								isSaving || !canManageForm || isSolutionManaged
							}
							onClick={() => setIsInfoDialogOpen(true)}
							title={formName ? "Edit Info" : "Set Info"}
							className="rounded-r-none"
						>
							<Pencil className="h-4 w-4" />
						</Button>
						<Button
							variant="outline"
							size="icon-lg"
							disabled={isSaving}
							onClick={() => setIsContextDialogOpen(true)}
							title="Show Context"
							className="rounded-none border-l-0"
						>
							<Info className="h-4 w-4" />
						</Button>
						{formId ? (
							<Button
								variant="outline"
								size="icon-lg"
								disabled={
									isSaving ||
									!canManageForm ||
									isSolutionManaged
								}
								onClick={() => setIsShareDialogOpen(true)}
								title="Share Form"
								className="rounded-none border-l-0"
							>
								<Share2 className="h-4 w-4" />
							</Button>
						) : null}
						{launchWorkflowId && (
							<Button
								variant="outline"
								size="icon-lg"
								onClick={() => {
									setTestError(null);
									if (launchWorkflowParameters.length > 0) {
										setWorkflowParamsDialogOpen(true);
									} else {
										handleTestLaunchWorkflow();
									}
								}}
								disabled={
									!formId ||
									isSaving ||
									isTestingWorkflow ||
									workflowsLoading ||
									workflowsError
								}
								title="Test Launch Workflow"
								className="rounded-none border-l-0"
							>
								<Play className="h-4 w-4" />
							</Button>
						)}
						<Button
							variant="default"
							size="lg"
							onClick={handleSave}
							disabled={
								isSaving ||
								isSaveDisabled ||
								isSolutionManaged ||
								createForm.isPending ||
								updateForm.isPending
							}
							title={
								createForm.isPending || updateForm.isPending
									? "Saving..."
									: !validationResult.valid
										? `Missing required parameters: ${validationResult.missingParams.join(", ")}`
										: "Save Form"
							}
							className="rounded-l-none border-l-0"
						>
							<Save className="h-4 w-4" />
							{isSaving ? "Saving…" : "Save"}
						</Button>
					</div>
				</div>
			</div>

			{isSolutionManaged && (
				<div className="px-1 pb-3">
					<SolutionManagedBanner entityLabel="form" />
				</div>
			)}

			{testError && !workflowParamsDialogOpen && (
				<Alert variant="destructive">
					<AlertDescription>{testError}</AlertDescription>
				</Alert>
			)}
			{saveError && (
				<Alert
					variant="destructive"
					ref={saveErrorRef}
					tabIndex={-1}
					className="outline-none"
				>
					<AlertTitle>
						{savePartial
							? "Finish saving access"
							: "Form could not be saved"}
					</AlertTitle>
					<AlertDescription className="space-y-3">
						<p className="whitespace-pre-wrap [overflow-wrap:anywhere]">
							{saveError}
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							disabled={isSaving}
							onClick={() => void handleSave()}
						>
							{isSaving ? "Saving…" : "Retry save"}
						</Button>
					</AlertDescription>
				</Alert>
			)}

			<Tabs
				inert={isSaving}
				aria-busy={isSaving}
				defaultValue="builder"
				className="flex w-full flex-1 flex-col lg:min-h-0 lg:overflow-hidden"
			>
				<TabsList className="flex-shrink-0">
					<TabsTrigger value="builder">Form Builder</TabsTrigger>
					<TabsTrigger value="preview">
						<Eye className="mr-2 h-4 w-4" />
						Preview
					</TabsTrigger>
				</TabsList>

				<TabsContent
					value="builder"
					inert={!canManageForm || isSolutionManaged}
					className="flex-1 lg:min-h-0 lg:overflow-hidden data-[state=active]:flex"
				>
					<FieldsPanelDnD
						fields={fields}
						setFields={setFields}
						linkedWorkflow={linkedWorkflow}
						previewContext={previewContext}
					/>
				</TabsContent>

				<TabsContent
					value="preview"
					className="flex-1 lg:min-h-0 lg:overflow-auto data-[state=active]:block"
				>
					<FormPreview
						formName={formName}
						formDescription={formDescription}
						fields={fields}
					/>
				</TabsContent>
			</Tabs>

			<FormInfoDialog
				open={isInfoDialogOpen}
				onClose={() => setIsInfoDialogOpen(false)}
				onSave={handleInfoSave}
				initialData={formInfo ?? existingForm}
				initialRoleIds={selectedRoleIds}
			/>

			{formId ? (
				<FormShareDialog
					formId={formId}
					formName={formName || "Form"}
					open={isShareDialogOpen}
					onOpenChange={setIsShareDialogOpen}
				/>
			) : null}

			<Dialog
				open={isContextDialogOpen}
				onOpenChange={setIsContextDialogOpen}
			>
				<DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[600px]">
					<DialogHeader className="shrink-0">
						<DialogTitle>Form Context Preview</DialogTitle>
						<DialogDescription>
							Preview of context available to form fields at
							runtime. Workflow values shown are based on your
							current session
							{workflowResults
								? " and test launch workflow results"
								: ""}
							.
						</DialogDescription>
					</DialogHeader>
					<div className="min-h-0 overflow-y-auto">
						<ContextViewer
							context={previewContext}
							maxHeight="500px"
							fieldNames={fields.map((f) => f.name)}
						/>
					</div>
				</DialogContent>
			</Dialog>

			<Dialog
				open={workflowParamsDialogOpen}
				onOpenChange={(open) => {
					if (!testBusy.current) setWorkflowParamsDialogOpen(open);
				}}
			>
				<DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[600px]">
					<DialogHeader className="shrink-0">
						<DialogTitle>Test Launch Workflow</DialogTitle>
						<DialogDescription>
							Enter parameters for {launchWorkflowId} to test with
							real workflow data.
						</DialogDescription>
					</DialogHeader>
					{testError && (
						<p
							role="alert"
							className="shrink-0 text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{testError}
						</p>
					)}
					<WorkflowParametersForm
						className="flex min-h-0 flex-col [&>div]:min-h-0 [&>div]:overflow-y-auto [&>button]:shrink-0 [&_input]:min-h-11 [&_button]:min-h-11"
						parameters={launchWorkflowParameters}
						onExecute={handleTestLaunchWorkflow}
						isExecuting={isTestingWorkflow}
						executeButtonText="Run & View Results"
					/>
				</DialogContent>
			</Dialog>

			<Dialog
				open={workflowResultsDialogOpen}
				onOpenChange={setWorkflowResultsDialogOpen}
			>
				<DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[700px]">
					<DialogHeader className="shrink-0">
						<DialogTitle>Launch Workflow Test Results</DialogTitle>
						<DialogDescription>
							Results from executing the launch workflow. This
							data will be available in context.workflow when the
							form loads.
						</DialogDescription>
					</DialogHeader>
					<div className="min-h-0 overflow-y-auto space-y-4">
						<div className="p-4 bg-muted rounded-md ring-1 ring-foreground/5">
							<pre className="text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">
								{JSON.stringify(workflowResults, null, 2)}
							</pre>
						</div>
						<p className="text-sm text-muted-foreground">
							The context preview has been updated with these
							results. You can now test field visibility
							expressions and HTML templates with real workflow
							data.
						</p>
					</div>
				</DialogContent>
			</Dialog>
		</div>
	);
}
