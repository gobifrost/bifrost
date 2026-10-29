import { useEffect, useId } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Form,
	FormControl,
	FormDescription,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@/components/ui/form";
import { Combobox } from "@/components/ui/combobox";
import { MultiCombobox } from "./MultiCombobox";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { useRoles } from "@/hooks/useRoles";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { AccessLevelSelect } from "@/components/access/AccessLevelSelect";
import type { components } from "@/lib/v1";

type WorkflowParameter = components["schemas"]["WorkflowParameter"];

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
type FormPublic = components["schemas"]["FormPublic"];

const formInfoSchema = z.object({
	name: z.string().min(1, "Name is required"),
	description: z.string(),
	workflow_id: z.string().min(1, "Linked workflow is required"),
	launch_workflow_id: z.string(),
	default_launch_params: z.record(z.string(), z.unknown()),
	access_level: z.enum(["authenticated", "everyone", "role_based"]),
	role_ids: z.array(z.string()),
	organization_id: z.string().nullable(),
});

export type FormInfoValues = z.infer<typeof formInfoSchema>;

interface FormInfoDialogProps {
	open: boolean;
	onClose: () => void;
	onSave: (info: FormInfoValues) => void;
	initialData?: Partial<FormPublic> | FormInfoValues | null;
	/** Role IDs currently assigned to this form */
	initialRoleIds?: string[];
}

export function FormInfoDialog({
	open,
	onClose,
	onSave,
	initialData,
	initialRoleIds,
}: FormInfoDialogProps) {
	const { user } = useAuth();
	const authorization = useAuthorization();

	const {
		data: metadata,
		isLoading: metadataLoading,
		isError: metadataError,
		refetch: refetchMetadata,
	} = useWorkflowsMetadata();
	const {
		data: roles,
		isLoading: rolesLoading,
		isError: rolesError,
		refetch: refetchRoles,
	} = useRoles();

	const defaultOrgId = user?.organizationId ?? null;

	const form = useForm<FormInfoValues>({
		resolver: zodResolver(formInfoSchema),
		defaultValues: {
			name: "",
			description: "",
			workflow_id: "",
			launch_workflow_id: "",
			default_launch_params: {},
			access_level: "role_based",
			role_ids: [],
			organization_id: defaultOrgId,
		},
	});

	const accessLevel = useWatch({
		control: form.control,
		name: "access_level",
	});
	const launchWorkflowId = useWatch({
		control: form.control,
		name: "launch_workflow_id",
	});
	const defaultLaunchParams = useWatch({
		control: form.control,
		name: "default_launch_params",
	});
	const selectedOrganizationId = useWatch({
		control: form.control,
		name: "organization_id",
	});
	const currentOrganizationId = initialData
		? (initialData.organization_id ?? null)
		: defaultOrgId;
	const canManageCurrentScope = authorization.canAt(
		"forms.readwrite",
		orgTarget(currentOrganizationId),
	);
	const canManageSelectedScope = authorization.canAt(
		"forms.readwrite",
		orgTarget(selectedOrganizationId),
	);
	const canChooseScope = authorization.canAnywhere("forms.readwrite");
	const knownOrganizations = [
		...(defaultOrgId
			? [{ id: defaultOrgId, name: "Your organization" }]
			: []),
		...(initialData?.organization_id
			? [{ id: initialData.organization_id, name: "Form organization" }]
			: []),
	];

	// Reset form when dialog opens or initialData changes
	useEffect(() => {
		if (open) {
			if (initialData) {
				form.reset({
					name: initialData.name || "",
					description: initialData.description || "",
					workflow_id: initialData.workflow_id || "",
					launch_workflow_id: initialData.launch_workflow_id || "",
					default_launch_params:
						(initialData.default_launch_params as Record<
							string,
							unknown
						>) || {},
					access_level:
						(initialData.access_level as
							"authenticated" | "everyone" | "role_based") ||
						"role_based",
					role_ids: initialRoleIds || [],
					organization_id:
						initialData.organization_id === undefined
							? defaultOrgId
							: initialData.organization_id,
				});
			} else {
				form.reset({
					name: "",
					description: "",
					workflow_id: "",
					launch_workflow_id: "",
					default_launch_params: {},
					access_level: "role_based",
					role_ids: [],
					organization_id: defaultOrgId,
				});
			}
		}
	}, [open, initialData, initialRoleIds, form, defaultOrgId]);

	// Get selected launch workflow metadata
	const selectedLaunchWorkflow = metadata?.workflows?.find(
		(w: WorkflowMetadata) => w.id === launchWorkflowId,
	);
	const launchWorkflowParams = selectedLaunchWorkflow?.parameters || [];

	const handleParameterChange = (paramName: string, value: unknown) => {
		const next = { ...form.getValues("default_launch_params") };
		if (value === undefined) delete next[paramName];
		else next[paramName] = value;
		form.setValue("default_launch_params", next);
	};

	const handleSave = (values: FormInfoValues) => {
		if (!canManageCurrentScope || !canManageSelectedScope) return;
		// Handle "__none__" special value for launch workflow
		const finalLaunchWorkflowId =
			values.launch_workflow_id === "__none__" ||
			!values.launch_workflow_id.trim()
				? ""
				: values.launch_workflow_id.trim();

		// Only include defaultLaunchParams if launch workflow is set and params exist
		const finalDefaultParams =
			finalLaunchWorkflowId &&
			Object.keys(values.default_launch_params).length > 0
				? values.default_launch_params
				: {};

		onSave({
			...values,
			launch_workflow_id: finalLaunchWorkflowId,
			default_launch_params: finalDefaultParams,
		});
		onClose();
	};

	const handleLaunchWorkflowChange = (value: string) => {
		const previous = form.getValues("launch_workflow_id");
		form.setValue("launch_workflow_id", value);
		if (previous !== value || !value || value === "__none__") {
			form.setValue("default_launch_params", {});
		}
	};

	return (
		<Dialog open={open} onOpenChange={onClose}>
			<DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[600px]">
				<DialogHeader className="shrink-0">
					<DialogTitle>Form Information</DialogTitle>
					<DialogDescription>
						Configure basic details about the form and linked
						workflow
					</DialogDescription>
				</DialogHeader>

				<Form {...form}>
					<form
						onSubmit={form.handleSubmit(handleSave)}
						className="flex min-h-0 min-w-0 flex-1 flex-col gap-4"
					>
						<div className="min-h-0 overflow-y-auto space-y-5">
							{metadataError && (
								<div
									role="alert"
									className="space-y-2 text-sm text-destructive"
								>
									Could not load the latest workflows.
									<Button
										type="button"
										variant="outline"
										className="min-h-11"
										onClick={() => void refetchMetadata()}
									>
										Retry workflows
									</Button>
								</div>
							)}
							{canChooseScope && (
								<FormField
									control={form.control}
									name="organization_id"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Organization</FormLabel>
											<FormControl>
												<OrganizationSelect
													triggerClassName="min-h-11 lg:min-h-11"
													value={field.value}
													onChange={field.onChange}
													showGlobal={true}
													loadOrganizations={authorization.canAnywhere(
														"organizations.read",
													)}
													knownOrganizations={
														knownOrganizations
													}
												/>
											</FormControl>
											<FormDescription>
												Global forms are available to
												all organizations
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>
							)}

							<FormField
								control={form.control}
								name="name"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Form Name *</FormLabel>
										<FormControl>
											<Input
												className="min-h-11"
												placeholder="User Onboarding Form"
												{...field}
											/>
										</FormControl>
										<FormMessage />
									</FormItem>
								)}
							/>

							<FormField
								control={form.control}
								name="workflow_id"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Linked Workflow *</FormLabel>
										<FormControl>
											<Combobox
												className="min-h-11 sm:min-h-11"
												value={field.value}
												onValueChange={field.onChange}
												options={
													metadata?.workflows?.map(
														(
															workflow: WorkflowMetadata,
														) => {
															const option: {
																value: string;
																label: string;
																description?: string;
															} = {
																value:
																	workflow.id ??
																	"",
																label:
																	workflow.name ??
																	"Unnamed",
															};
															if (
																workflow.description
															) {
																option.description =
																	workflow.description;
															}
															return option;
														},
													) ?? []
												}
												placeholder="Select a workflow"
												searchPlaceholder="Search workflows..."
												emptyText="No workflows found."
												isLoading={metadataLoading}
											/>
										</FormControl>
										<FormDescription>
											The workflow that will be executed
											when this form is submitted
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>

							<FormField
								control={form.control}
								name="description"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Description</FormLabel>
										<FormControl>
											<Textarea
												placeholder="Describe what this form does..."
												rows={3}
												{...field}
											/>
										</FormControl>
										<FormMessage />
									</FormItem>
								)}
							/>

							<FormField
								control={form.control}
								name="access_level"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Access Level</FormLabel>
										<FormControl>
											<AccessLevelSelect
												className="min-h-11 sm:min-h-11"
												value={field.value}
												onValueChange={field.onChange}
											/>
										</FormControl>
										<FormDescription>
											Controls who can view and execute
											this form
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>

							{accessLevel === "role_based" && (
								<FormField
									control={form.control}
									name="role_ids"
									render={({ field }) => (
										<FormItem>
											<FormLabel>
												Assigned Roles{" "}
												{field.value.length > 0 &&
													`(${field.value.length})`}
											</FormLabel>
											<FormControl>
												<MultiCombobox
													options={(roles ?? []).map(
														(role) => ({
															value: role.id,
															label: role.name,
															description:
																role.description ??
																undefined,
														}),
													)}
													value={field.value}
													onValueChange={
														field.onChange
													}
													isLoading={rolesLoading}
													placeholder="Select roles..."
													searchPlaceholder="Search roles..."
												/>
											</FormControl>
											{rolesError && (
												<div
													role="alert"
													className="space-y-2 text-sm text-destructive"
												>
													Could not load the latest
													roles. Your selection is
													preserved.
													<Button
														type="button"
														variant="outline"
														className="min-h-11"
														onClick={() =>
															void refetchRoles()
														}
													>
														Retry roles
													</Button>
												</div>
											)}
											<FormDescription>
												Users must have at least one of
												these roles to access the form
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>
							)}

							<FormField
								control={form.control}
								name="launch_workflow_id"
								render={({ field }) => (
									<FormItem>
										<FormLabel>
											Launch Workflow (Optional)
										</FormLabel>
										<FormControl>
											<Combobox
												className="min-h-11 sm:min-h-11"
												value={field.value}
												onValueChange={
													handleLaunchWorkflowChange
												}
												options={[
													{
														value: "__none__",
														label: "None",
													},
													...(metadata?.workflows?.map(
														(
															workflow: WorkflowMetadata,
														) => {
															const option: {
																value: string;
																label: string;
																description?: string;
															} = {
																value:
																	workflow.id ??
																	"",
																label:
																	workflow.name ??
																	"Unnamed",
															};
															if (
																workflow.description
															) {
																option.description =
																	workflow.description;
															}
															return option;
														},
													) ?? []),
												]}
												placeholder="Select a workflow (or leave empty)"
												searchPlaceholder="Search workflows..."
												emptyText="No workflows found."
												isLoading={metadataLoading}
											/>
										</FormControl>
										<FormDescription>
											Workflow to execute when form loads
											(results available in
											context.workflow)
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>

							{/* Default Launch Parameters */}
							{launchWorkflowId &&
								launchWorkflowId !== "__none__" &&
								launchWorkflowParams.length > 0 && (
									<div className="min-w-0 space-y-4 border-t pt-5">
										<div>
											<Label className="text-sm font-medium">
												Default Launch Parameters
											</Label>
											<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground mt-1">
												Set default values for workflow
												parameters. Required parameters
												must have either a default value
												or a form field with "Allow as
												Query Param" enabled.
											</p>
										</div>
										<div className="space-y-3">
											{launchWorkflowParams.map(
												(param: WorkflowParameter) => (
													<div key={param.name}>
														<LaunchParameterField
															param={param}
															value={
																defaultLaunchParams[
																	param.name ??
																		""
																]
															}
															onChange={
																handleParameterChange
															}
														/>
													</div>
												),
											)}
										</div>
									</div>
								)}
						</div>
						<DialogFooter className="shrink-0">
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								onClick={onClose}
							>
								Cancel
							</Button>
							<Button className="min-h-11" type="submit">
								Save
							</Button>
						</DialogFooter>
					</form>
				</Form>
			</DialogContent>
		</Dialog>
	);
}

function LaunchParameterField({
	param,
	value,
	onChange,
}: {
	param: WorkflowParameter;
	value: unknown;
	onChange: (name: string, value: unknown) => void;
}) {
	const id = useId();
	switch (param.type) {
		case "bool":
			return (
				<div className="flex items-center space-x-2">
					<Checkbox
						id={`${id}-${param.name}`}
						checked={!!value}
						onCheckedChange={(checked) =>
							onChange(param.name ?? "", checked)
						}
					/>
					<Label
						htmlFor={`${id}-${param.name}`}
						className="min-h-11 flex flex-wrap items-center text-sm font-normal [overflow-wrap:anywhere]"
					>
						{param.name}
						{param.required && (
							<span className="text-destructive ml-1">*</span>
						)}
						{!param.required && (
							<Badge
								variant="secondary"
								className="text-xs px-1 py-0 ml-2"
							>
								Optional
							</Badge>
						)}
						{param.description && (
							<span className="block text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground mt-1">
								{param.description}
							</span>
						)}
					</Label>
				</div>
			);

		case "int":
		case "float":
			return (
				<div className="space-y-1.5">
					<Label
						htmlFor={`${id}-${param.name}`}
						className="text-sm flex flex-wrap items-center gap-2"
					>
						{param.name}
						{param.required && (
							<Badge
								variant="destructive"
								className="text-xs px-1 py-0"
							>
								Required
							</Badge>
						)}
						{!param.required && (
							<Badge
								variant="secondary"
								className="text-xs px-1 py-0"
							>
								Optional
							</Badge>
						)}
					</Label>
					<Input
						className="min-h-11"
						id={`${id}-${param.name}`}
						type="number"
						step={param.type === "float" ? "0.1" : "1"}
						value={(value as string | number | undefined) ?? ""}
						onChange={(e) =>
							onChange(
								param.name ?? "",
								e.target.value === ""
									? undefined
									: param.type === "int"
										? parseInt(e.target.value)
										: parseFloat(e.target.value),
							)
						}
						placeholder={
							param.description ||
							`Enter default value for ${param.name}`
						}
					/>
					{param.description && (
						<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
							{param.description}
						</p>
					)}
				</div>
			);

		case "list":
			return (
				<div className="space-y-1.5">
					<Label
						htmlFor={`${id}-${param.name}`}
						className="text-sm flex flex-wrap items-center gap-2"
					>
						{param.name}
						{param.required && (
							<Badge
								variant="destructive"
								className="text-xs px-1 py-0"
							>
								Required
							</Badge>
						)}
						{!param.required && (
							<Badge
								variant="secondary"
								className="text-xs px-1 py-0"
							>
								Optional
							</Badge>
						)}
					</Label>
					<Input
						className="min-h-11"
						id={`${id}-${param.name}`}
						type="text"
						value={
							Array.isArray(value)
								? value.join(", ")
								: ((value as string) ?? "")
						}
						onChange={(e) =>
							onChange(
								param.name ?? "",
								e.target.value.split(",").map((v) => v.trim()),
							)
						}
						placeholder={
							param.description || "Comma-separated values"
						}
					/>
					{param.description && (
						<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
							{param.description}
						</p>
					)}
				</div>
			);

		default:
			// string, email, json
			return (
				<div className="space-y-1.5">
					<Label
						htmlFor={`${id}-${param.name}`}
						className="text-sm flex flex-wrap items-center gap-2"
					>
						{param.name}
						{param.required && (
							<Badge
								variant="destructive"
								className="text-xs px-1 py-0"
							>
								Required
							</Badge>
						)}
						{!param.required && (
							<Badge
								variant="secondary"
								className="text-xs px-1 py-0"
							>
								Optional
							</Badge>
						)}
					</Label>
					<Input
						className="min-h-11"
						id={`${id}-${param.name}`}
						type={param.type === "email" ? "email" : "text"}
						value={(value as string) ?? ""}
						onChange={(e) =>
							onChange(param.name ?? "", e.target.value)
						}
						placeholder={
							param.description ||
							`Enter default value for ${param.name}`
						}
					/>
					{param.description && (
						<p className="text-sm leading-6 [overflow-wrap:anywhere] text-muted-foreground">
							{param.description}
						</p>
					)}
				</div>
			);
	}
}
