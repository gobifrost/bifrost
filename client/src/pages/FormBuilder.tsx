import { useState, useMemo } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { parseSolutionFrom } from "@/lib/solution-back-nav";
import { ArrowLeft, Save, Eye, Pencil, Info, Play } from "lucide-react";
import { SolutionManagedBanner } from "@/components/solutions/SolutionManagedBanner";
import { Button } from "@/components/ui/button";
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
import type { FormInfoValues } from "@/components/forms/FormInfoDialog";
import { FieldsPanelDnD } from "@/components/forms/FieldsPanelDnD";
import { FormPreview } from "@/components/forms/FormPreview";
import { WorkflowParametersForm } from "@/components/workflows/WorkflowParametersForm";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { useAuth } from "@/contexts/AuthContext";
import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";
type FormCreate = components["schemas"]["FormCreate"];
type FormUpdate = components["schemas"]["FormUpdate"];
type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
import { toast } from "sonner";

export function FormBuilder() {
	const navigate = useNavigate();
	const { formId } = useParams();
	const { search } = useLocation();
	const fromSolution = parseSolutionFrom(search);
	const backTo = fromSolution ? `/solutions/${fromSolution}` : "/forms";
	const isEditing = !!formId;
	const { scope } = useOrgScope();
	const { user, isPlatformAdmin } = useAuth();

	const { data: existingForm } = useFormQuery(formId);
	const createForm = useCreateForm();
	const updateForm = useUpdateForm();
	const { data: workflowsMetadata } = useWorkflowsMetadata();

	// Solution-managed forms are read-only on the platform (criterion 6).
	const isSolutionManaged = existingForm?.is_solution_managed ?? false;

	const defaultOrgId = isPlatformAdmin
		? null
		: (user?.organizationId ?? null);

	// Lightweight state for form metadata (set when dialog saves, or synced from existingForm)
	const [formInfo, setFormInfo] = useState<FormInfoValues | null>(null);

	// Fields state for the drag-and-drop builder (separate from dialog metadata)
	const [fields, setFields] = useState<FormField[]>([]);

	// UI state
	const [isInfoDialogOpen, setIsInfoDialogOpen] = useState(() => !isEditing);
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

	// Sync fields and formInfo when existingForm loads. Adjusting state
	// during render with a "previous existingForm" sentinel is the React-
	// recommended idiom for prop-driven resets and avoids an extra effect
	// render cycle.
	const [prevExistingFormId, setPrevExistingFormId] = useState<
		string | null
	>(null);
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
					(existingForm.default_launch_params as Record<string, unknown>) || {},
				access_level:
					(existingForm.access_level as "authenticated" | "role_based") || "role_based",
				role_ids: [],
				organization_id: existingForm.organization_id ?? defaultOrgId,
			});
		}
	}

	// Derive display values from formInfo (dialog-saved) or existingForm (server data)
	const formName = formInfo?.name || existingForm?.name || "";
	const formDescription = formInfo?.description || existingForm?.description || "";
	const linkedWorkflow = formInfo?.workflow_id || existingForm?.workflow_id || "";
	const launchWorkflowId = formInfo?.launch_workflow_id || existingForm?.launch_workflow_id || "";
	const defaultLaunchParams = formInfo?.default_launch_params ||
		(existingForm?.default_launch_params as Record<string, unknown>) || {};
	const accessLevel = formInfo?.access_level ||
		(existingForm?.access_level as "authenticated" | "role_based") || "role_based";
	const selectedRoleIds = formInfo?.role_ids || [];
	const organizationId = formInfo?.organization_id ??
		existingForm?.organization_id ?? defaultOrgId;
	const isGlobal = organizationId === null;

	const handleInfoSave = (info: FormInfoValues) => {
		setFormInfo(info);
	};

	const handleSave = async () => {
		try {
			// Auto-generate allowedQueryParams from fields that have allow_as_query_param enabled
			const autoGeneratedParams = fields
				.filter((field) => field.allow_as_query_param === true)
				.map((field) => field.name);

			if (isEditing && formId) {
				const updateRequest: FormUpdate = {
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
				};
				await updateForm.mutateAsync({
					params: { path: { form_id: formId } },
					body: updateRequest,
				});

				if (accessLevel === "role_based") {
					await assignRolesToForm(formId, selectedRoleIds);
				} else {
					await assignRolesToForm(formId, []);
				}
			} else {
				const createRequest: FormCreate = {
					name: formName,
					description: formDescription || null,
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

				if (
					accessLevel === "role_based" &&
					createdForm?.id &&
					selectedRoleIds.length > 0
				) {
					await assignRolesToForm(createdForm.id, selectedRoleIds);
				}
			}

			navigate("/forms");
		} catch (error: unknown) {
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
				errorResponse?.message ||
				"Failed to save form";
			const errorDetails = errorResponse?.response?.data?.details;

			if (errorDetails?.errors) {
				const validationErrors = errorDetails.errors
					.map(
						(err: { loc: string[]; msg: string }) =>
							`${err.loc.join(".")}: ${err.msg}`,
					)
					.join("\n");
				toast.error(`Validation Error\n${validationErrors}`, {
					duration: 8000,
				});
			} else {
				toast.error(errorMessage, {
					duration: errorMessage.length > 150 ? 10000 : 6000,
				});
			}
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
		!formName ||
		!linkedWorkflow ||
		fields.length === 0 ||
		!validationResult.valid;

	// Handle test launch workflow execution
	const handleTestLaunchWorkflow = async () => {
		if (!launchWorkflowId) {
			toast.error("No launch workflow configured");
			return;
		}

		if (!formId) {
			toast.error(
				"Please save the form first before testing the launch workflow",
			);
			return;
		}

		try {
			setIsTestingWorkflow(true);

			const inputData = defaultLaunchParams || {};
			const response = await executeFormStartup(formId, inputData);

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
			toast.error(errorMessage);
		} finally {
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

	return (
		<div className="flex flex-col h-full -m-6 lg:-m-8 p-6 lg:p-8">
			<div className="flex items-center justify-between flex-shrink-0 mb-6">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						{formName || (isEditing ? "Edit Form" : "New Form")}
					</h1>
					<div className="mt-2 flex items-center gap-2">
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
						{formDescription && (
							<p className="text-sm text-muted-foreground">
								{formDescription}
							</p>
						)}
					</div>
				</div>
				<div className="flex items-center gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => navigate(backTo)}
						title={fromSolution ? "Back to Solution" : "Back to Forms"}
					>
						<ArrowLeft className="h-4 w-4" />
					</Button>
					<div className="flex items-center">
						<Button
							variant="outline"
							size="icon"
							onClick={() => setIsInfoDialogOpen(true)}
							title={formName ? "Edit Info" : "Set Info"}
							className="rounded-r-none"
						>
							<Pencil className="h-4 w-4" />
						</Button>
						<Button
							variant="outline"
							size="icon"
							onClick={() => setIsContextDialogOpen(true)}
							title="Show Context"
							className="rounded-none border-l-0"
						>
							<Info className="h-4 w-4" />
						</Button>
						{launchWorkflowId && (
							<Button
								variant="outline"
								size="icon"
								onClick={() => {
									if (launchWorkflowParameters.length > 0) {
										setWorkflowParamsDialogOpen(true);
									} else {
										handleTestLaunchWorkflow();
									}
								}}
								disabled={!formId || isTestingWorkflow}
								title="Test Launch Workflow"
								className="rounded-none border-l-0"
							>
								<Play className="h-4 w-4" />
							</Button>
						)}
						<Button
							variant="outline"
							size="icon"
							onClick={handleSave}
							disabled={
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
						</Button>
					</div>
				</div>
			</div>

			{isSolutionManaged && (
				<div className="px-1 pb-3">
					<SolutionManagedBanner entityLabel="form" />
				</div>
			)}

			<Tabs
				defaultValue="builder"
				className="w-full flex-1 flex flex-col overflow-hidden"
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
					className="flex-1 overflow-hidden data-[state=active]:flex"
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
					className="flex-1 overflow-auto data-[state=active]:block"
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
				formId={formId}
				onSave={handleInfoSave}
				initialData={existingForm}
				initialRoleIds={selectedRoleIds}
				isEditing={isEditing}
			/>

			<Dialog
				open={isContextDialogOpen}
				onOpenChange={setIsContextDialogOpen}
			>
				<DialogContent className="sm:max-w-[600px]">
					<DialogHeader>
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
					<ContextViewer
						context={previewContext}
						maxHeight="500px"
						fieldNames={fields.map((f) => f.name)}
					/>
				</DialogContent>
			</Dialog>

			<Dialog
				open={workflowParamsDialogOpen}
				onOpenChange={setWorkflowParamsDialogOpen}
			>
				<DialogContent className="sm:max-w-[600px]">
					<DialogHeader>
						<DialogTitle>Test Launch Workflow</DialogTitle>
						<DialogDescription>
							Enter parameters for {launchWorkflowId} to test with
							real workflow data.
						</DialogDescription>
					</DialogHeader>
					<WorkflowParametersForm
						parameters={launchWorkflowParameters}
						onExecute={async () => {
							await handleTestLaunchWorkflow();
							setWorkflowParamsDialogOpen(false);
						}}
						isExecuting={isTestingWorkflow}
						executeButtonText="Run & View Results"
					/>
				</DialogContent>
			</Dialog>

			<Dialog
				open={workflowResultsDialogOpen}
				onOpenChange={setWorkflowResultsDialogOpen}
			>
				<DialogContent className="sm:max-w-[700px]">
					<DialogHeader>
						<DialogTitle>Launch Workflow Test Results</DialogTitle>
						<DialogDescription>
							Results from executing the launch workflow. This
							data will be available in context.workflow when the
							form loads.
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-4">
						<div className="p-4 bg-muted rounded-md">
							<pre className="text-xs overflow-auto max-h-[400px]">
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
