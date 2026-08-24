import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
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
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
	PLATFORM_BOUNDARY_HEADERS,
	useAuthorizationCapabilities,
	useCreateRole,
	useUpdateRole,
} from "@/hooks/useRoles";
import type { components } from "@/lib/v1";
type Role = components["schemas"]["RolePublic"];
type Capability = components["schemas"]["AuthorizationCapabilityPublic"];

const formSchema = z.object({
	name: z.string().min(1, "Name is required").max(100, "Name too long"),
	description: z.string().optional(),
	capabilities: z.array(z.string()),
});

type FormValues = z.infer<typeof formSchema>;

interface RoleDialogProps {
	role?: Role | undefined;
	open: boolean;
	onClose: () => void;
}

export function RoleDialog({ role, open, onClose }: RoleDialogProps) {
	const [capabilitySearch, setCapabilitySearch] = useState("");
	const createRole = useCreateRole();
	const updateRole = useUpdateRole();
	const {
		data: capabilityCatalogData = [],
		isLoading: capabilitiesLoading,
		isError: capabilitiesError,
	} = useAuthorizationCapabilities();
	const isEditing = !!role;
	const isBuiltin = role?.is_builtin ?? false;
	const capabilityCatalog = capabilityCatalogData as Capability[];
	const visibleCapabilities = useMemo(() => {
		const allowed = capabilityCatalog.filter(
			(capability) =>
				capability.assignable_to_custom_roles ||
				role?.capabilities?.includes(capability.key),
		);
		const query = capabilitySearch.trim().toLocaleLowerCase();
		if (!query) return allowed;
		return allowed.filter((capability) =>
			[
				capability.display_name,
				capability.key,
				capability.description,
				capability.category,
			].some(
				(value) => value.toLocaleLowerCase().includes(query),
			),
		);
	}, [role?.capabilities, capabilityCatalog, capabilitySearch]);

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			name: "",
			description: "",
			capabilities: [],
		},
	});

	useEffect(() => {
		if (role) {
			form.reset({
				name: role.name,
				description: role.description || "",
				capabilities: role.capabilities ?? [],
			});
		} else {
			form.reset({
				name: "",
				description: "",
				capabilities: [],
			});
		}
	}, [role, form]);

	const onSubmit = async (values: FormValues) => {
		if (isBuiltin) return;
		if (isEditing) {
			await updateRole.mutateAsync({
				headers: PLATFORM_BOUNDARY_HEADERS,
				params: { path: { role_id: role.id } },
				body: {
					name: values.name,
					description: values.description || null,
					capabilities: values.capabilities,
				},
			});
		} else {
			await createRole.mutateAsync({
				headers: PLATFORM_BOUNDARY_HEADERS,
				body: {
					name: values.name,
					description: values.description || null,
					capabilities: values.capabilities,
				},
			});
		}
		setCapabilitySearch("");
		onClose();
	};

	const closeDialog = () => {
		setCapabilitySearch("");
		onClose();
	};

	const isPending = createRole.isPending || updateRole.isPending;

	return (
		<Dialog open={open} onOpenChange={(next) => !next && closeDialog()}>
			<DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-[680px]">
				<DialogHeader>
					<DialogTitle>
						{isBuiltin ? "Built-in Role" : isEditing ? "Edit Role" : "Create Role"}
					</DialogTitle>
					<DialogDescription>
						{isBuiltin
							? "This role and its capabilities are managed by Bifrost."
							: isEditing
							? "Update the role information"
							: "Create a new role for organization users"}
					</DialogDescription>
				</DialogHeader>

				<Form {...form}>
					<form
						onSubmit={form.handleSubmit(onSubmit)}
						className="space-y-4"
					>
						<FormField
							control={form.control}
							name="name"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Role Name</FormLabel>
									<FormControl>
										<Input
											placeholder="Admin, Viewer, Editor..."
											disabled={isBuiltin}
											{...field}
										/>
									</FormControl>
									<FormDescription>
										A descriptive name for this role
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
									<FormLabel>
										Description (Optional)
									</FormLabel>
									<FormControl>
										<Textarea
											placeholder="What should people with this role be able to do?"
											disabled={isBuiltin}
											{...field}
										/>
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>

						<div className="border-t pt-4">
							<div className="mb-3">
								<h4 className="text-sm font-medium">Platform capabilities</h4>
								<p className="mt-1 text-xs leading-5 text-muted-foreground">
									Choose the actions this role may perform. Resource access is
									still controlled separately by organization and resource roles.
								</p>
							</div>
							<Input
								value={capabilitySearch}
								onChange={(event) => setCapabilitySearch(event.target.value)}
								placeholder="Search capabilities…"
								aria-label="Search capabilities"
							/>
							{capabilitiesLoading ? (
								<p className="text-sm text-muted-foreground">Loading capabilities…</p>
							) : capabilitiesError ? (
								<p className="text-sm text-destructive">
									Capabilities could not be loaded. Close this dialog and try again.
								</p>
							) : (
								<div
									className="max-h-56 space-y-2 overflow-auto pr-1"
									role="region"
									aria-label="Capability list"
								>
									{visibleCapabilities.map((capability: Capability) => (
											<FormField
												key={capability.key}
												control={form.control}
												name="capabilities"
												render={({ field }) => {
													const checked = field.value.includes(capability.key);
													const disabled =
														isBuiltin || !capability.assignable_to_custom_roles;
													return (
														<label className="flex cursor-pointer items-start gap-3 rounded-2xl bg-muted/40 p-3 ring-1 ring-foreground/5 has-disabled:cursor-default">
															<Checkbox
																className="mt-0.5"
																checked={checked}
																disabled={disabled}
																onCheckedChange={(value) =>
																	field.onChange(
																		value
																			? [...field.value, capability.key]
																			: field.value.filter(
																					(key) => key !== capability.key,
																				),
																	)
																}
																aria-label={capability.display_name}
															/>
															<span className="min-w-0 flex-1">
																<span className="flex flex-wrap items-center gap-2 text-sm font-medium">
																	{capability.display_name}
																	{capability.is_privileged ? (
																		<Badge variant="outline" className="font-normal">
																			Privileged
																		</Badge>
																	) : null}
																</span>
																<code className="text-xs text-muted-foreground">{capability.key}</code>
																<span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
																	{capability.description}
																</span>
															</span>
														</label>
													);
												}}
											/>
										))}
									{visibleCapabilities.length === 0 ? (
										<p className="py-6 text-center text-sm text-muted-foreground">
											No capabilities match your search.
										</p>
									) : null}
								</div>
							)}
						</div>

						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={closeDialog}
							>
								{isBuiltin ? "Close" : "Cancel"}
							</Button>
							{!isBuiltin ? <Button type="submit" disabled={isPending || capabilitiesLoading || capabilitiesError}>
								{isPending
									? "Saving..."
									: isEditing
										? "Update"
										: "Create"}
							</Button> : null}
						</DialogFooter>
					</form>
				</Form>
			</DialogContent>
		</Dialog>
	);
}
