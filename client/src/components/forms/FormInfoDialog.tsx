import { useEffect, useState } from "react";
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
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { useRoles } from "@/hooks/useRoles";
import { useAuth } from "@/contexts/AuthContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { FormEmbedSection } from "@/components/forms/FormEmbedSection";
import type { components } from "@/lib/v1";

type WorkflowParameter = components["schemas"]["WorkflowParameter"];
type Role = components["schemas"]["RolePublic"];
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
	initialData?: FormPublic | null;
	/** Whether this is editing an existing form (org cannot be changed) */
	isEditing?: boolean;
	/** Form ID for embed settings (only available when editing) */
	formId?: string;
	/** Role IDs currently assigned to this form */
	initialRoleIds?: string[];
}

export function FormInfoDialog({
	open,
	onClose,
	onSave,
	initialData,
	isEditing = false,
	formId,
	initialRoleIds,
}: FormInfoDialogProps) {
	const [rolesPopoverOpen, setRolesPopoverOpen] = useState(false);
	const { isPlatformAdmin, user } = useAuth();

	const { data: metadata, isLoading: metadataLoading } =
		useWorkflowsMetadata() as {
			data?: { workflows?: WorkflowMetadata[] };
			isLoading: boolean;
		};

	const { data: roles, isLoading: rolesLoading } = useRoles();

	const defaultOrgId = isPlatformAdmin
		? null
		: (user?.organizationId ?? null);

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

	const accessLevel = useWatch({ control: form.control, name: "access_level" });
	const launchWorkflowId = useWatch({ control: form.control, name: "launch_workflow_id" });
	const defaultLaunchParams = useWatch({ control: form.control, name: "default_launch_params" });
	const selectedRoleIds = useWatch({ control: form.control, name: "role_ids" });

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
						(initialData.default_launch_params as Record<string, unknown>) || {},
					access_level:
						(initialData.access_level as "authenticated" | "everyone" | "role_based") ||
						"role_based",
					role_ids: initialRoleIds || [],
					organization_id: initialData.organization_id ?? defaultOrgId,
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
		const current = form.getValues("default_launch_params");
		form.setValue("default_launch_params", {
			...current,
			[paramName]: value,
		});
	};

	const renderParameterInput = (param: WorkflowParameter) => {
		const value = defaultLaunchParams[param.name ?? ""];

		switch (param.type) {
			case "bool":
				return (
					<div className="flex items-center space-x-2">
						<Checkbox
							id={`param-${param.name}`}
							checked={!!value}
							onCheckedChange={(checked) =>
								handleParameterChange(param.name ?? "", checked)
							}
						/>
						<Label
							htmlFor={`param-${param.name}`}
							className="text-sm font-normal"
						>
							{param.name}
							{param.required && (
								<span className="text-destructive ml-1">*</span>
							)}
							{!param.required && (
								<Badge
									variant="secondary"
									className="text-[10px] px-1 py-0 ml-2"
								>
									Optional
								</Badge>
							)}
							{param.description && (
								<span className="block text-xs text-muted-foreground mt-1">
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
							htmlFor={`param-${param.name}`}
							className="text-sm flex items-center gap-2"
						>
							{param.name}
							{param.required && (
								<Badge
									variant="destructive"
									className="text-[10px] px-1 py-0"
								>
									Required
								</Badge>
							)}
							{!param.required && (
								<Badge
									variant="secondary"
									className="text-[10px] px-1 py-0"
								>
									Optional
								</Badge>
							)}
						</Label>
						<Input
							id={`param-${param.name}`}
							type="number"
							step={param.type === "float" ? "0.1" : "1"}
							value={(value as string | number | undefined) ?? ""}
							onChange={(e) =>
								handleParameterChange(
									param.name ?? "",
									param.type === "int"
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
							<p className="text-xs text-muted-foreground">
								{param.description}
							</p>
						)}
					</div>
				);

			case "list":
				return (
					<div className="space-y-1.5">
						<Label
							htmlFor={`param-${param.name}`}
							className="text-sm flex items-center gap-2"
						>
							{param.name}
							{param.required && (
								<Badge
									variant="destructive"
									className="text-[10px] px-1 py-0"
								>
									Required
								</Badge>
							)}
							{!param.required && (
								<Badge
									variant="secondary"
									className="text-[10px] px-1 py-0"
								>
									Optional
								</Badge>
							)}
						</Label>
						<Input
							id={`param-${param.name}`}
							type="text"
							value={
								Array.isArray(value)
									? value.join(", ")
									: ((value as string) ?? "")
							}
							onChange={(e) =>
								handleParameterChange(
									param.name ?? "",
									e.target.value
										.split(",")
										.map((v) => v.trim()),
								)
							}
							placeholder={
								param.description || "Comma-separated values"
							}
						/>
						{param.description && (
							<p className="text-xs text-muted-foreground">
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
							htmlFor={`param-${param.name}`}
							className="text-sm flex items-center gap-2"
						>
							{param.name}
							{param.required && (
								<Badge
									variant="destructive"
									className="text-[10px] px-1 py-0"
								>
									Required
								</Badge>
							)}
							{!param.required && (
								<Badge
									variant="secondary"
									className="text-[10px] px-1 py-0"
								>
									Optional
								</Badge>
							)}
						</Label>
						<Input
							id={`param-${param.name}`}
							type={param.type === "email" ? "email" : "text"}
							value={(value as string) ?? ""}
							onChange={(e) =>
								handleParameterChange(
									param.name ?? "",
									e.target.value,
								)
							}
							placeholder={
								param.description ||
								`Enter default value for ${param.name}`
							}
						/>
						{param.description && (
							<p className="text-xs text-muted-foreground">
								{param.description}
							</p>
						)}
					</div>
				);
		}
	};

	const handleSave = (values: FormInfoValues) => {
		// Handle "__none__" special value for launch workflow
		const finalLaunchWorkflowId =
			values.launch_workflow_id === "__none__" || !values.launch_workflow_id.trim()
				? ""
				: values.launch_workflow_id.trim();

		// Only include defaultLaunchParams if launch workflow is set and params exist
		const finalDefaultParams =
			finalLaunchWorkflowId && Object.keys(values.default_launch_params).length > 0
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
		form.setValue("launch_workflow_id", value);
		if (!value || value === "__none__") {
			form.setValue("default_launch_params", {});
		}
	};

	const toggleRole = (roleId: string) => {
		const current = form.getValues("role_ids");
		if (current.includes(roleId)) {
			form.setValue("role_ids", current.filter((id) => id !== roleId));
		} else {
			form.setValue("role_ids", [...current, roleId]);
		}
	};

	const removeRole = (roleId: string) => {
		const current = form.getValues("role_ids");
		form.setValue("role_ids", current.filter((id) => id !== roleId));
	};

	return (
		<Dialog open={open} onOpenChange={onClose}>
			<DialogContent className="sm:max-w-[600px]">
				<DialogHeader>
					<DialogTitle>Form Information</DialogTitle>
					<DialogDescription>
						Configure basic details about the form and linked
						workflow
					</DialogDescription>
				</DialogHeader>

				<Form {...form}>
					<form
						onSubmit={form.handleSubmit(handleSave)}
						className="space-y-4"
					>
						{/* Organization Scope - Only show for platform admins */}
						{isPlatformAdmin && (
							<FormField
								control={form.control}
								name="organization_id"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Organization</FormLabel>
										<FormControl>
											<OrganizationSelect
												value={field.value}
												onChange={field.onChange}
												showGlobal={true}
											/>
										</FormControl>
										<FormDescription>
											Global forms are available to all organizations
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
											value={field.value}
											onValueChange={field.onChange}
											options={
												metadata?.workflows?.map(
													(workflow: WorkflowMetadata) => {
														const option: {
															value: string;
															label: string;
															description?: string;
														} = {
															value: workflow.id ?? "",
															label: workflow.name ?? "Unnamed",
														};
														if (workflow.description) {
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
										The workflow that will be executed when this form is
										submitted
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
										<Combobox
											value={field.value}
											onValueChange={field.onChange}
											options={[
												{
													value: "role_based",
													label: "Role-Based",
													description:
														"Only users with assigned roles can access",
												},
												{
													value: "authenticated",
													label: "Everyone except external users",
													description:
														"Any signed-in user except external users",
												},
												{
													value: "everyone",
													label: "Everyone",
													description:
														"Any signed-in user, including external users",
												},
											]}
											placeholder="Select access level"
										/>
									</FormControl>
									<FormDescription>
										Controls who can view and execute this form
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
										<Popover
											open={rolesPopoverOpen}
											onOpenChange={setRolesPopoverOpen}
										>
											<PopoverTrigger asChild>
												<FormControl>
													<Button
														variant="outline"
														role="combobox"
														className="w-full justify-between font-normal"
														disabled={rolesLoading}
													>
														<span className="text-muted-foreground">
															{rolesLoading
																? "Loading roles..."
																: "Select roles..."}
														</span>
														<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
													</Button>
												</FormControl>
											</PopoverTrigger>
											<PopoverContent
												className="w-[var(--radix-popover-trigger-width)] p-0"
												align="start"
											>
												<Command>
													<CommandInput placeholder="Search roles..." />
													<CommandList>
														<CommandEmpty>
															No roles found.
														</CommandEmpty>
														<CommandGroup>
															{roles?.map((role: Role) => (
																<CommandItem
																	key={role.id}
																	value={role.name || ""}
																	onSelect={() =>
																		toggleRole(role.id)
																	}
																>
																	<div className="flex items-center gap-2 flex-1">
																		<Checkbox
																			checked={selectedRoleIds.includes(
																				role.id,
																			)}
																			onCheckedChange={() =>
																				toggleRole(
																					role.id,
																				)
																			}
																		/>
																		<div className="flex flex-col">
																			<span className="font-medium">
																				{role.name}
																			</span>
																			{role.description && (
																				<span className="text-xs text-muted-foreground">
																					{
																						role.description
																					}
																				</span>
																			)}
																		</div>
																	</div>
																	<Check
																		className={cn(
																			"ml-auto h-4 w-4",
																			selectedRoleIds.includes(
																				role.id,
																			)
																				? "opacity-100"
																				: "opacity-0",
																		)}
																	/>
																</CommandItem>
															))}
														</CommandGroup>
													</CommandList>
												</Command>
											</PopoverContent>
										</Popover>
										{selectedRoleIds.length > 0 && (
											<div className="flex flex-wrap gap-2 p-2 border rounded-md bg-muted/50">
												{selectedRoleIds.map((roleId) => {
													const role = roles?.find(
														(r: Role) => r.id === roleId,
													);
													return (
														<Badge
															key={roleId}
															variant="secondary"
															className="gap-1"
														>
															{role?.name || roleId}
															<X
																className="h-3 w-3 cursor-pointer"
																onClick={() =>
																	removeRole(roleId)
																}
															/>
														</Badge>
													);
												})}
											</div>
										)}
										<FormDescription>
											Users must have at least one of these roles to
											access the form
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
											value={field.value}
											onValueChange={handleLaunchWorkflowChange}
											options={[
												{
													value: "__none__",
													label: "None",
												},
												...(metadata?.workflows?.map(
													(workflow: WorkflowMetadata) => {
														const option: {
															value: string;
															label: string;
															description?: string;
														} = {
															value: workflow.id ?? "",
															label: workflow.name ?? "Unnamed",
														};
														if (workflow.description) {
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
										Workflow to execute when form loads (results
										available in context.workflow)
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>

						{/* Default Launch Parameters */}
						{launchWorkflowId &&
							launchWorkflowId !== "__none__" &&
							launchWorkflowParams.length > 0 && (
								<div className="space-y-3 rounded-lg border p-4 bg-muted/50">
									<div>
										<Label className="text-sm font-medium">
											Default Launch Parameters
										</Label>
										<p className="text-xs text-muted-foreground mt-1">
											Set default values for workflow
											parameters. Required parameters must
											have either a default value or a form
											field with "Allow as Query Param"
											enabled.
										</p>
									</div>
									<div className="space-y-3">
										{launchWorkflowParams.map(
											(param: WorkflowParameter) => (
												<div key={param.name}>
													{renderParameterInput(param)}
												</div>
											),
										)}
									</div>
								</div>
							)}

						<DialogFooter>
							<Button type="button" variant="outline" onClick={onClose}>
								Cancel
							</Button>
							<Button type="submit">
								Save
							</Button>
						</DialogFooter>
					</form>
				</Form>

				{isEditing && formId && (
					<div className="min-w-0 overflow-hidden">
						<FormEmbedSection formId={formId} />
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}
