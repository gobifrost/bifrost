import { useEffect, useRef } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { getErrorMessage } from "@/lib/api-error";
import { Loader2 } from "lucide-react";
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
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSetConfig, useUpdateConfig } from "@/hooks/useConfig";
import { useAuth } from "@/contexts/AuthContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";

type Config = components["schemas"]["ConfigResponse"];

const createFormSchema = (isEditing: boolean) =>
	z
		.object({
			key: z
				.string()
				.min(1, "Key is required")
				.regex(
					/^[a-zA-Z0-9_]+$/,
					"Key must be alphanumeric with underscores",
				),
			value: z.string(),
			type: z.enum(["string", "int", "bool", "json", "secret"]),
			description: z.string().optional(),
			organization_id: z.string().nullable(),
		})
		.refine(
			(data) => {
				// When editing secrets, value is optional (empty = keep existing)
				if (isEditing && data.type === "secret") return true;
				// Otherwise value is required
				return data.value.length > 0;
			},
			{ message: "Value is required", path: ["value"] },
		);

type FormValues = z.infer<ReturnType<typeof createFormSchema>>;

interface ConfigDialogProps {
	config?: Config | undefined;
	open: boolean;
	onClose: () => void;
}

export function ConfigDialog({ config, open, onClose }: ConfigDialogProps) {
	const setConfig = useSetConfig({ showErrorToast: false });
	const updateConfig = useUpdateConfig({ showErrorToast: false });
	const { user } = useAuth();
	const authorization = useAuthorization();
	const isEditing = !!config;
	const submitBusy = useRef(false);
	const saveErrorRef = useRef<HTMLDivElement>(null);

	const defaultOrgId = user?.organizationId ?? null;

	const form = useForm<FormValues>({
		resolver: zodResolver(createFormSchema(isEditing)),
		defaultValues: {
			key: "",
			value: "",
			type: "string",
			description: "",
			organization_id: defaultOrgId,
		},
	});

	// Watch the type field to conditionally render input type
	const selectedType = useWatch({ control: form.control, name: "type" });
	const selectedOrganizationId = useWatch({
		control: form.control,
		name: "organization_id",
	});
	const currentOrganizationId = config ? config.org_id : defaultOrgId;
	const canManageCurrentScope = authorization.canAt(
		"configs.readwrite",
		orgTarget(currentOrganizationId),
	);
	const canManageSelectedScope = authorization.canAt(
		"configs.readwrite",
		orgTarget(selectedOrganizationId),
	);
	const canChooseScope = authorization.canAnywhere("configs.readwrite");
	const knownOrganizations = [
		...(defaultOrgId
			? [{ id: defaultOrgId, name: "Your organization" }]
			: []),
		...(config?.org_id
			? [{ id: config.org_id, name: "Configuration organization" }]
			: []),
	];

	useEffect(() => {
		if (config) {
			form.reset({
				key: config.key,
				// For secrets, we don't show the actual value - user must enter new value to update
				value:
					config.type === "secret"
						? ""
						: typeof config.value === "object" &&
							  config.value !== null
							? JSON.stringify(config.value, null, 2)
							: String(config.value ?? ""),
				type: config.type,
				description: config.description ?? "",
				organization_id: config.org_id ?? null,
			});
		} else {
			form.reset({
				key: "",
				value: "",
				type: "string",
				description: "",
				organization_id: defaultOrgId,
			});
		}
	}, [config, form, open, defaultOrgId]);

	const onSubmit = async (values: FormValues) => {
		if (
			submitBusy.current ||
			isSaving ||
			!canManageCurrentScope ||
			!canManageSelectedScope
		)
			return;
		submitBusy.current = true;
		form.clearErrors("root.save");
		try {
			if (isEditing && config.id) {
				// Omit an unchanged secret so the existing encrypted value is preserved.
				const updateBody = {
					key: values.key,
					...(values.type === "secret" && !values.value
						? {}
						: { value: values.value }),
					type: values.type,
					description: values.description ?? null,
					organization_id: values.organization_id,
				};
				await updateConfig.mutateAsync({
					params: { path: { config_id: config.id } },
					body: updateBody,
				});
			} else {
				await setConfig.mutateAsync({
					body: {
						key: values.key,
						value: values.value,
						type: values.type,
						description: values.description ?? null,
						organization_id: values.organization_id,
					},
				});
			}
			onClose();
		} catch (error) {
			form.setError("root.save", {
				type: "server",
				message: getErrorMessage(
					error,
					"Try saving again. Your changes are preserved.",
				),
			});
		} finally {
			submitBusy.current = false;
		}
	};

	const isSaving = setConfig.isPending || updateConfig.isPending;

	const saveError = form.formState.errors.root?.save?.message;
	useEffect(() => {
		if (saveError) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [saveError]);

	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!nextOpen && !isSaving) onClose();
			}}
		>
			<DialogContent
				className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[600px]"
				showCloseButton={!isSaving}
				onEscapeKeyDown={(event) => {
					if (isSaving) event.preventDefault();
				}}
				onInteractOutside={(event) => {
					if (isSaving) event.preventDefault();
				}}
			>
				<DialogHeader>
					<DialogTitle>
						{isEditing ? "Edit Configuration" : "Add Configuration"}
					</DialogTitle>
					<DialogDescription>
						{isEditing
							? "Update the configuration value"
							: "Create a new configuration entry"}
					</DialogDescription>
				</DialogHeader>

				<Form {...form}>
					<form
						onSubmit={(event) =>
							void form.handleSubmit(onSubmit)(event)
						}
						className="flex min-h-0 flex-col gap-4"
					>
						<div
							role="region"
							aria-label="Configuration settings"
							className="min-h-0 overflow-y-auto pr-1"
						>
							<fieldset
								disabled={isSaving}
								className="min-w-0 space-y-4"
							>
								{canChooseScope && (
									<FormField
										control={form.control}
										name="organization_id"
										render={({ field }) => (
											<FormItem>
												<FormLabel>
													Organization
												</FormLabel>
												<FormControl>
													<OrganizationSelect
														value={field.value}
														onChange={
															field.onChange
														}
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
													Global config is available
													to all organizations
												</FormDescription>
												<FormMessage />
											</FormItem>
										)}
									/>
								)}

								<FormField
									control={form.control}
									name="key"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Key</FormLabel>
											<FormControl>
												<Input
													placeholder="CONFIG_KEY_NAME"
													{...field}
													disabled={isEditing}
													className="min-h-11 font-mono lg:min-h-0"
												/>
											</FormControl>
											<FormDescription>
												Alphanumeric characters and
												underscores only
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>

								<FormField
									control={form.control}
									name="type"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Type</FormLabel>
											<Select
												onValueChange={field.onChange}
												value={field.value}
												disabled={
													isSaving ||
													!canManageCurrentScope ||
													!canManageSelectedScope
												}
											>
												<FormControl>
													<SelectTrigger className="min-h-11 lg:min-h-0">
														<SelectValue placeholder="Select type" />
													</SelectTrigger>
												</FormControl>
												<SelectContent>
													<SelectItem value="string">
														String
													</SelectItem>
													<SelectItem value="int">
														Integer
													</SelectItem>
													<SelectItem value="bool">
														Boolean
													</SelectItem>
													<SelectItem value="json">
														JSON
													</SelectItem>
													<SelectItem value="secret">
														Secret
													</SelectItem>
												</SelectContent>
											</Select>
											<FormDescription>
												{selectedType === "secret" &&
													"Secret values are encrypted at rest"}
											</FormDescription>
											<FormMessage />
										</FormItem>
									)}
								/>

								<FormField
									control={form.control}
									name="value"
									render={({ field }) => (
										<FormItem>
											<FormLabel>Value</FormLabel>
											<FormControl>
												{selectedType === "secret" ? (
													<Input
														type="password"
														placeholder={
															isEditing
																? "Leave empty to keep existing..."
																: "Enter secret value"
														}
														className="min-h-11 font-mono lg:min-h-0"
														{...field}
													/>
												) : (
													<Textarea
														placeholder="Configuration value"
														className="min-h-11 font-mono lg:min-h-0"
														{...field}
													/>
												)}
											</FormControl>
											<FormDescription>
												{selectedType === "secret"
													? isEditing
														? "Leave empty to keep the existing secret, or enter a new value to update"
														: "Secret will be encrypted and stored securely"
													: "Enter the configuration value"}
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
												<Input
													placeholder="What is this config for?"
													className="min-h-11 lg:min-h-0"
													{...field}
												/>
											</FormControl>
											<FormMessage />
										</FormItem>
									)}
								/>
							</fieldset>
						</div>
						{form.formState.errors.root?.save && (
							<Alert
								variant="destructive"
								ref={saveErrorRef}
								tabIndex={-1}
								className="max-h-32 shrink-0 overflow-y-auto outline-none"
							>
								<AlertTitle>
									Configuration could not be saved
								</AlertTitle>
								<AlertDescription>
									{form.formState.errors.root.save.message}
								</AlertDescription>
							</Alert>
						)}
						<DialogFooter className="shrink-0">
							<Button
								type="button"
								variant="outline"
								onClick={onClose}
								disabled={isSaving}
								className="min-h-11 lg:min-h-0"
							>
								Cancel
							</Button>
							<Button
								type="submit"
								className="min-h-11 lg:min-h-0"
								disabled={isSaving}
							>
								{isSaving && (
									<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
								)}
								{isSaving
									? "Saving..."
									: isEditing
										? "Update"
										: "Create"}
							</Button>
						</DialogFooter>
					</form>
				</Form>
			</DialogContent>
		</Dialog>
	);
}
