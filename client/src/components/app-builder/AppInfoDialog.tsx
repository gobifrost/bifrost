/**
 * App Info Dialog Component
 *
 * Unified dialog for creating and editing applications.
 * Handles form state, validation, and API mutations.
 */

import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import {
	Loader2,
	Check,
	ChevronsUpDown,
	X,
	ChevronDown,
	ChevronRight,
	ArrowRightLeft,
	AppWindow,
} from "lucide-react";
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
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { LogoDropZone } from "@/components/LogoDropZone";
import { bumpEntityLogo } from "@/components/entityLogoVersions";
import { AppReplacePathDialog } from "@/components/applications/AppReplacePathDialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox } from "@/components/ui/combobox";
import { cn } from "@/lib/utils";
import { useRoles } from "@/hooks/useRoles";
import { useAuth } from "@/contexts/AuthContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import {
	useApplication,
	useCreateApplication,
	useDeleteApplication,
	useUpdateApplication,
} from "@/hooks/useApplications";
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
import { Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import type { components } from "@/lib/v1";

type RolePublic = components["schemas"]["RolePublic"];

const ACCESS_LEVELS = [
	{
		value: "role_based",
		label: "Role-Based",
		description: "Only users with assigned roles can access",
	},
	{
		value: "authenticated",
		label: "Authenticated Users",
		description: "Any authenticated user can access",
	},
];

const formSchema = z.object({
	name: z
		.string()
		.min(1, "Name is required")
		.max(255, "Name must be 255 characters or less"),
	slug: z
		.string()
		.min(1, "Slug is required")
		.max(255, "Slug must be 255 characters or less")
		.regex(
			/^[a-z][a-z0-9-]*$/,
			"Slug must start with a letter and contain only lowercase letters, numbers, and hyphens",
		),
	description: z.string().optional(),
	organization_id: z.string().nullable(),
	access_level: z.enum(["authenticated", "role_based"]),
	role_ids: z.array(z.string()),
});

type FormValues = z.infer<typeof formSchema>;

interface AppInfoDialogProps {
	appSlug?: string | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** Called after successful create with the new app slug */
	onCreated?: (slug: string) => void;
}

export function AppInfoDialog({
	appSlug,
	open,
	onOpenChange,
	onCreated,
}: AppInfoDialogProps) {
	const isEditing = !!appSlug;
	const { isPlatformAdmin, user } = useAuth();

	const { data: existingApp, isLoading: isLoadingApp } = useApplication(
		isEditing ? appSlug : undefined,
	);

	const { data: roles, isLoading: rolesLoading } = useRoles();
	const createApplication = useCreateApplication();
	const updateApplication = useUpdateApplication();
	const deleteApplication = useDeleteApplication();
	const navigate = useNavigate();

	const [rolesPopoverOpen, setRolesPopoverOpen] = useState(false);
	const [slugManuallyEdited, setSlugManuallyEdited] = useState(false);
	const [advancedOpen, setAdvancedOpen] = useState(false);
	const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
	const [replacePathOpen, setReplacePathOpen] = useState(false);

	// Default organization_id for org users is their org, for platform admins it's null (global)
	const defaultOrgId = isPlatformAdmin
		? null
		: (user?.organizationId ?? null);

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			name: "",
			slug: "",
			description: "",
			organization_id: defaultOrgId,
			access_level: "role_based",
			role_ids: [],
		},
	});

	const accessLevel = useWatch({ control: form.control, name: "access_level" });

	// Load existing app data when editing
	useEffect(() => {
		if (existingApp && isEditing) {
			form.reset({
				name: existingApp.name,
				slug: existingApp.slug,
				description: existingApp.description ?? "",
				organization_id: existingApp.organization_id ?? null,
				access_level: (existingApp.access_level as "authenticated" | "role_based") || "authenticated",
				role_ids: existingApp.role_ids ?? [],
			});
		} else if (!isEditing && open) {
			form.reset({
				name: "",
				slug: "",
				description: "",
					organization_id: defaultOrgId,
				access_level: "role_based",
				role_ids: [],
			});
		}
	}, [existingApp, isEditing, form, open, defaultOrgId]);

	// Auto-generate slug from name (only when creating and not manually edited)
	const handleNameChange = (newName: string) => {
		form.setValue("name", newName);
		if (!slugManuallyEdited && !isEditing) {
			const generated = newName
				.toLowerCase()
				.replace(/[^a-z0-9]+/g, "-")
				.replace(/^-|-$/g, "");
			form.setValue("slug", generated);
		}
	};

	const handleSlugChange = (newSlug: string) => {
		form.setValue("slug", newSlug);
		setSlugManuallyEdited(true);
	};

	const handleClose = () => {
		form.reset();
		setSlugManuallyEdited(false);
		setAdvancedOpen(false);
		onOpenChange(false);
	};

	const onSubmit = async (values: FormValues) => {
		try {
			if (isEditing && existingApp) {
				// Check if slug changed - we'll need to update the URL
				const slugChanged = values.slug !== existingApp.slug;

				await updateApplication.mutateAsync({
					params: {
						path: { app_id: existingApp.id },
					},
					body: {
						name: values.name,
						slug: values.slug,
						description: values.description || null,
						access_level: values.access_level,
						role_ids: values.role_ids,
						// scope is passed in body for platform admins
						scope: isPlatformAdmin
							? (values.organization_id ?? "global")
							: undefined,
					},
				});
				handleClose();

				// If slug changed, trigger navigation callback with new slug
				if (slugChanged) {
					onCreated?.(values.slug);
				}
			} else {
				const result = await createApplication.mutateAsync({
					body: {
						name: values.name,
						slug: values.slug,
						description: values.description || null,
						access_level: values.access_level,
						role_ids: values.role_ids,
						organization_id: values.organization_id || null,
					},
				});
				handleClose();
				onCreated?.(result.slug);
			}
		} catch {
			// Error handling is done by the mutation hooks via toast
		}
	};

	const toggleRole = (roleId: string) => {
		const current = form.getValues("role_ids");
		if (current.includes(roleId)) {
			form.setValue(
				"role_ids",
				current.filter((id) => id !== roleId),
			);
		} else {
			form.setValue("role_ids", [...current, roleId]);
		}
	};

	const removeRole = (roleId: string) => {
		const current = form.getValues("role_ids");
		form.setValue(
			"role_ids",
			current.filter((id) => id !== roleId),
		);
	};

	const isPending = createApplication.isPending || updateApplication.isPending;
	const selectedRoleIds = useWatch({ control: form.control, name: "role_ids" });

	return (
		<Dialog open={open} onOpenChange={handleClose}>
			<DialogContent className="sm:max-w-[500px]">
				<DialogHeader>
					<div className="flex items-start gap-4">
						{isEditing && existingApp ? (
							<LogoDropZone
								uploadUrl={`/api/applications/${existingApp.id}/logo`}
								deleteUrl={`/api/applications/${existingApp.id}/logo`}
								previewUrl={`/api/applications/${existingApp.id}/logo`}
								fallback={<AppWindow className="h-6 w-6" />}
								size={64}
								onChange={() =>
									bumpEntityLogo("app", existingApp.id)
								}
							/>
						) : null}
						<div className="min-w-0 flex-1">
							<DialogTitle>
								{isEditing
									? "Edit Application"
									: "Create Application"}
							</DialogTitle>
							<DialogDescription>
								{isEditing
									? "Update the application settings"
									: "Configure your new application"}
							</DialogDescription>
						</div>
					</div>
				</DialogHeader>

				{isEditing && isLoadingApp ? (
					<div className="flex items-center justify-center py-8">
						<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
					</div>
				) : (
					<Form {...form}>
						<form
							onSubmit={form.handleSubmit(onSubmit)}
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
												{isEditing
													? "Warning: Changing the organization will move the app to a different scope"
													: "Global apps are available to all organizations"}
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>
							)}

							{/* Name */}
							<FormField
								control={form.control}
								name="name"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Name</FormLabel>
										<FormControl>
											<Input
												placeholder="My Application"
												{...field}
												onChange={(e) =>
													handleNameChange(e.target.value)
												}
											/>
										</FormControl>
										<FormMessage />
									</FormItem>
								)}
							/>

							{/* Slug */}
							<FormField
								control={form.control}
								name="slug"
								render={({ field }) => (
									<FormItem>
										<FormLabel>URL Slug</FormLabel>
										<FormControl>
											<Input
												placeholder="my-application"
												{...field}
												onChange={(e) =>
													handleSlugChange(e.target.value)
												}
											/>
										</FormControl>
										<FormDescription>
											{isEditing
												? "Warning: Changing the slug will change the app's URL"
												: `Your app will be accessible at /apps/${field.value || "..."}`}
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>

							{/* Description */}
							<FormField
								control={form.control}
								name="description"
								render={({ field }) => (
									<FormItem>
										<FormLabel>Description</FormLabel>
										<FormControl>
											<Textarea
												placeholder="A brief description of your application..."
												rows={3}
												{...field}
											/>
										</FormControl>
										<FormMessage />
									</FormItem>
								)}
							/>

							{/* Access Level */}
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
												options={ACCESS_LEVELS}
												placeholder="Select access level"
											/>
										</FormControl>
										<FormDescription>
											Controls who can view and use this application
										</FormDescription>
										<FormMessage />
									</FormItem>
								)}
							/>

							{/* Role Selection - Only show when role_based */}
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
															aria-expanded={rolesPopoverOpen}
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
																{roles?.map((role: RolePublic) => (
																	<CommandItem
																		key={role.id}
																		value={role.name || ""}
																		onSelect={() =>
																			toggleRole(role.id)
																		}
																	>
																		<div className="flex items-center gap-2 flex-1">
																			<Checkbox
																				checked={field.value.includes(
																					role.id,
																				)}
																				onCheckedChange={() =>
																					toggleRole(role.id)
																				}
																			/>
																			<div className="flex flex-col">
																				<span className="font-medium">
																					{role.name}
																				</span>
																				{role.description && (
																					<span className="text-xs text-muted-foreground">
																						{role.description}
																					</span>
																				)}
																			</div>
																		</div>
																		<Check
																			className={cn(
																				"ml-auto h-4 w-4",
																				field.value.includes(role.id)
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
															(r: RolePublic) => r.id === roleId,
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
																	onClick={() => removeRole(roleId)}
																/>
															</Badge>
														);
													})}
												</div>
											)}
											<FormDescription>
												Users must have at least one of these roles to
												access the application
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>
							)}

							{isEditing && existingApp && (
								<Collapsible
									open={advancedOpen}
									onOpenChange={setAdvancedOpen}
									className="border-t pt-4"
								>
									<CollapsibleTrigger asChild>
										<button
											type="button"
											className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
										>
											{advancedOpen ? (
												<ChevronDown className="h-3 w-3" />
											) : (
												<ChevronRight className="h-3 w-3" />
											)}
											Advanced
										</button>
									</CollapsibleTrigger>
									<CollapsibleContent className="pt-3 space-y-2">
										<label className="text-sm font-medium">
											Source path
										</label>
										<div className="flex items-center gap-2">
											<code className="flex-1 bg-muted px-2 py-1.5 rounded text-xs font-mono truncate">
												{existingApp.repo_path}
											</code>
											<Button
												type="button"
												variant="outline"
												size="sm"
												onClick={() => setReplacePathOpen(true)}
											>
												<ArrowRightLeft className="h-3 w-3 mr-1" />
												Replace…
											</Button>
										</div>
										<p className="text-xs text-muted-foreground">
											Repoint this app at a different source
											directory. Only do this after moving or renaming
											files.
										</p>
									</CollapsibleContent>
								</Collapsible>
							)}

							<DialogFooter className="pt-4 sm:justify-between">
								{isEditing && existingApp ? (
									<Button
										type="button"
										variant="ghost"
										className="text-destructive hover:text-destructive hover:bg-destructive/10"
										onClick={() => setConfirmDeleteOpen(true)}
										disabled={deleteApplication.isPending}
									>
										<Trash2 className="mr-2 h-4 w-4" />
										Delete
									</Button>
								) : (
									<span />
								)}
								<div className="flex gap-2">
									<Button
										type="button"
										variant="outline"
										onClick={handleClose}
									>
										Cancel
									</Button>
									<Button type="submit" disabled={isPending}>
										{isPending && (
											<Loader2 className="mr-2 h-4 w-4 animate-spin" />
										)}
										{isPending
											? "Saving..."
											: isEditing
												? "Save Changes"
												: "Create Application"}
									</Button>
								</div>
							</DialogFooter>
						</form>
					</Form>
				)}
			</DialogContent>
			{isEditing && existingApp && (
				<AppReplacePathDialog
					app={existingApp}
					open={replacePathOpen}
					onClose={() => setReplacePathOpen(false)}
				/>
			)}
			{isEditing && existingApp && (
				<AlertDialog
					open={confirmDeleteOpen}
					onOpenChange={setConfirmDeleteOpen}
				>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>Delete Application?</AlertDialogTitle>
							<AlertDialogDescription>
								This will permanently delete{" "}
								<strong>{existingApp.name}</strong> and all of
								its files. This action cannot be undone.
							</AlertDialogDescription>
						</AlertDialogHeader>
						<AlertDialogFooter>
							<AlertDialogCancel>Cancel</AlertDialogCancel>
							<AlertDialogAction
								className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
								onClick={async () => {
									await deleteApplication.mutateAsync({
										params: {
											path: { app_id: existingApp.id },
										},
									});
									setConfirmDeleteOpen(false);
									onOpenChange(false);
									// If the user was on the code editor for this
									// app, drop them back to the apps list.
									if (
										window.location.pathname.startsWith(
											`/apps/${existingApp.slug}`,
										)
									) {
										navigate("/apps");
									}
								}}
							>
								Delete
							</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
			)}
		</Dialog>
	);
}
