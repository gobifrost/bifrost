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
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
	useAuthorizationScopes,
	useCreateRole,
	useUpdateRole,
} from "@/hooks/useRoles";
import type { components } from "@/lib/v1";
type Role = components["schemas"]["RolePublic"];

const formSchema = z.object({
	name: z.string().min(1, "Name is required").max(100, "Name too long"),
	description: z.string().optional(),
	can_promote_agent: z.boolean(),
	scopes: z.array(z.string()),
});

type FormValues = z.infer<typeof formSchema>;

interface RoleDialogProps {
	role?: Role | undefined;
	open: boolean;
	onClose: () => void;
}

export function RoleDialog({ role, open, onClose }: RoleDialogProps) {
	const [scopeSearch, setScopeSearch] = useState("");
	const createRole = useCreateRole();
	const updateRole = useUpdateRole();
	const {
		data: scopeCatalog = [],
		isLoading: scopesLoading,
		isError: scopesError,
	} = useAuthorizationScopes();
	const isEditing = !!role;
	const isBuiltin = role?.is_builtin ?? false;
	const filteredScopeCatalog = useMemo(() => {
		const roleScopeCatalog = scopeCatalog.filter(
			(scope) =>
				scope.assignable_to_custom_roles ||
				role?.scopes?.includes(scope.key) ||
				scope.key === "platform.superuser",
		);
		const query = scopeSearch.trim().toLocaleLowerCase();
		if (!query) return roleScopeCatalog;
		return roleScopeCatalog.filter((scope) =>
			[
				scope.display_name,
				scope.key,
				scope.description,
				scope.category,
			].some((value) => value.toLocaleLowerCase().includes(query)),
		);
	}, [role?.scopes, scopeCatalog, scopeSearch]);

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			name: "",
			description: "",
			can_promote_agent: false,
			scopes: [],
		},
	});

	useEffect(() => {
		if (role) {
			form.reset({
				name: role.name,
				description: role.description || "",
				can_promote_agent: (role.permissions as Record<string, boolean>)?.can_promote_agent ?? false,
				scopes: role.scopes ?? [],
			});
		} else {
			form.reset({
				name: "",
				description: "",
				can_promote_agent: false,
				scopes: [],
			});
		}
	}, [role, form]);

	const onSubmit = async (values: FormValues) => {
		if (isBuiltin) return;
		if (isEditing) {
			await updateRole.mutateAsync({
				params: { path: { role_id: role.id } },
				body: {
					name: values.name,
					description: values.description || null,
					permissions: { can_promote_agent: values.can_promote_agent },
					scopes: values.scopes,
				},
			});
		} else {
			await createRole.mutateAsync({
				body: {
					name: values.name,
					description: values.description || null,
					permissions: { can_promote_agent: values.can_promote_agent },
					scopes: values.scopes,
				},
			});
		}
		setScopeSearch("");
		onClose();
	};

	const closeDialog = () => {
		setScopeSearch("");
		onClose();
	};

	const isPending = createRole.isPending || updateRole.isPending;

	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!nextOpen) closeDialog();
			}}
		>
			<DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-[680px]">
				<DialogHeader>
					<DialogTitle>
						{isBuiltin
							? "Built-in Role"
							: isEditing
								? "Edit Role"
								: "Create Role"}
					</DialogTitle>
					<DialogDescription>
						{isBuiltin
							? "This role and its scopes are managed by Bifrost."
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
											placeholder="What permissions does this role have?"
											disabled={isBuiltin}
											{...field}
										/>
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>

						<div className="space-y-3 border-t pt-4">
							<div>
								<h4 className="text-sm font-medium">Scopes</h4>
								<p className="mt-1 text-xs text-muted-foreground">
									Scopes grant platform capabilities. Organization reach and
									resource access are still checked separately.
								</p>
							</div>
							<Input
								value={scopeSearch}
								onChange={(event) => setScopeSearch(event.target.value)}
								placeholder="Search scopes..."
								aria-label="Search scopes"
							/>
							{scopesLoading ? (
								<p className="text-sm text-muted-foreground">
									Loading scopes…
								</p>
							) : scopesError ? (
								<p className="text-sm text-destructive">
									Scopes could not be loaded. Close this dialog and try again.
								</p>
							) : (
								<FormField
									control={form.control}
									name="scopes"
									render={({ field }) => (
										<div
											className="max-h-64 space-y-2 overflow-y-auto pr-1"
											role="region"
											aria-label="Scope list"
										>
											{filteredScopeCatalog.map((scope, index) => {
												const checked = field.value.includes(scope.key);
												const disabled =
													isBuiltin || !scope.assignable_to_custom_roles;
												return (
													<div key={scope.key} className="space-y-2">
														{filteredScopeCatalog[index - 1]?.category !==
															scope.category && (
															<h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
																{scope.category}
															</h5>
														)}
														<label className="flex items-start gap-3 rounded-lg border p-3">
															<Checkbox
																checked={checked}
																disabled={disabled}
																onCheckedChange={(next) => {
																	field.onChange(
																		next
																			? [...field.value, scope.key]
																			: field.value.filter(
																					(key) => key !== scope.key,
																				),
																	);
																}}
																aria-label={scope.display_name}
															/>
															<span className="min-w-0 flex-1">
																<span className="flex flex-wrap items-center gap-2 text-sm font-medium">
																	{scope.display_name}
																	{scope.is_privileged && (
																		<Badge
																			variant="outline"
																			className="font-normal"
																		>
																			Privileged
																		</Badge>
																	)}
																</span>
																<code className="text-xs text-muted-foreground">
																	{scope.key}
																</code>
																<span className="mt-1 block text-xs text-muted-foreground">
																	{scope.description}
																</span>
																{!scope.assignable_to_custom_roles &&
																	!checked && (
																		<span className="mt-1 block text-xs text-muted-foreground">
																			Reserved for a built-in role
																		</span>
																	)}
															</span>
														</label>
													</div>
												);
											})}
											{filteredScopeCatalog.length === 0 && (
												<p className="py-6 text-center text-sm text-muted-foreground">
													No scopes match your search.
												</p>
											)}
										</div>
									)}
								/>
							)}
						</div>

						{/* Permissions Section */}
						<div className="pt-4 border-t">
							<h4 className="text-sm font-medium mb-3">
								Resource permissions
							</h4>
							<FormField
								control={form.control}
								name="can_promote_agent"
								render={({ field }) => (
									<FormItem className="flex items-center justify-between rounded-lg bg-muted/50 p-3 ring-1 ring-foreground/5">
										<div className="space-y-0.5">
											<FormLabel className="text-sm">
												Promote Agents
											</FormLabel>
											<FormDescription className="text-xs">
												Allow users to promote private agents to the organization
											</FormDescription>
										</div>
										<FormControl>
											<Switch
												checked={field.value}
												onCheckedChange={field.onChange}
												disabled={isBuiltin}
											/>
										</FormControl>
									</FormItem>
								)}
							/>
						</div>

						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={closeDialog}
							>
								{isBuiltin ? "Close" : "Cancel"}
							</Button>
							{!isBuiltin && (
								<Button
									type="submit"
									disabled={isPending || scopesLoading || scopesError}
								>
									{isPending
										? "Saving..."
										: isEditing
											? "Update"
											: "Create"}
								</Button>
							)}
						</DialogFooter>
					</form>
				</Form>
			</DialogContent>
		</Dialog>
	);
}
