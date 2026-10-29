import { useEffect, useRef, useState } from "react";
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
import { Table2, X } from "lucide-react";
import {
	Form,
	FormControl,
	FormDescription,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@/components/ui/form";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { getErrorMessage } from "@/lib/api-error";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";
import { useCreateTable, useUpdateTable } from "@/services/tables";
import type { TablePublic } from "@/services/tables";
import { SolutionManagedBanner } from "@/components/solutions/SolutionManagedBanner";
import type { components } from "@/lib/v1";
import { PolicyEditor } from "./PolicyEditor";
import { CodeEditor } from "./CodeEditor";

type TablePolicies = components["schemas"]["TablePolicies"];

const tableNameRegex = /^[a-z][a-z0-9_-]*$/;

const formSchema = z.object({
	name: z
		.string()
		.min(1, "Name is required")
		.max(255, "Name too long")
		.regex(
			tableNameRegex,
			"Name must start with a lowercase letter and contain only lowercase letters, numbers, underscores, and hyphens",
		),
	description: z.string().optional(),
	schema: z.string().optional(),
	organization_id: z.string().nullable(),
});

type FormValues = z.infer<typeof formSchema>;

interface TableDialogProps {
	table?: TablePublic | undefined;
	open: boolean;
	onClose: () => void;
	embedded?: boolean;
	onBusyChange?: (busy: boolean) => void;
}

export function TableDialog({
	table,
	open,
	onClose,
	embedded = false,
	onBusyChange,
}: TableDialogProps) {
	const createTable = useCreateTable();
	const updateTable = useUpdateTable();
	const { user } = useAuth();
	const authorization = useAuthorization();
	const isEditing = !!table;
	// Solution-managed tables are read-only on the platform: deploy owns schema +
	// policies (criterion 6). Row data stays editable elsewhere (criterion 7).
	const isSolutionManaged = table?.is_solution_managed ?? false;

	const defaultOrgId = user?.organizationId ?? null;

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			name: "",
			description: "",
			schema: "",
			organization_id: defaultOrgId,
		},
	});

	const [policies, setPolicies] = useState<TablePolicies | null>(
		table?.policies ?? null,
	);
	// Reset policies when the dialog opens again, including a new-table draft.
	const currentPolicyKey = `${open}:${table?.id ?? "__new__"}`;
	const [lastPolicyKey, setLastPolicyKey] = useState(currentPolicyKey);

	if (currentPolicyKey !== lastPolicyKey) {
		setLastPolicyKey(currentPolicyKey);
		setPolicies(table?.policies ?? null);
	}

	const [policyParseError, setPolicyParseError] = useState<string | null>(
		null,
	);
	const selectedOrganizationId = useWatch({
		control: form.control,
		name: "organization_id",
	});
	const currentOrganizationId = table ? table.organization_id : defaultOrgId;
	const canManageCurrentScope = authorization.canAt(
		"tables.readwrite",
		orgTarget(currentOrganizationId),
	);
	const canManageSelectedScope = authorization.canAt(
		"tables.readwrite",
		orgTarget(selectedOrganizationId),
	);
	const canChooseScope = authorization.canAnywhere("tables.readwrite");
	const knownOrganizations = [
		...(defaultOrgId
			? [{ id: defaultOrgId, name: "Your organization" }]
			: []),
		...(table?.organization_id
			? [{ id: table.organization_id, name: "Table organization" }]
			: []),
	];

	useEffect(() => {
		if (table) {
			form.reset({
				name: table.name,
				description: table.description || "",
				schema: table.schema
					? JSON.stringify(table.schema, null, 2)
					: "",
				organization_id: table.organization_id ?? null,
			});
		} else {
			form.reset({
				name: "",
				description: "",
				schema: "",
				organization_id: defaultOrgId,
			});
		}
	}, [table, form, open, defaultOrgId]);

	const submitBusy = useRef(false);
	const saveErrorRef = useRef<HTMLDivElement>(null);
	const onSubmit = async (values: FormValues) => {
		if (
			submitBusy.current ||
			isPending ||
			isSolutionManaged ||
			policyParseError ||
			!canManageCurrentScope ||
			!canManageSelectedScope
		)
			return;
		form.clearErrors("root.save");
		let parsedSchema: Record<string, unknown> | null = null;
		if (values.schema && values.schema.trim()) {
			try {
				parsedSchema = JSON.parse(values.schema);
			} catch {
				form.setError("schema", {
					type: "manual",
					message: "Invalid JSON",
				});
				return;
			}
		}

		// Convert org ID to scope string: null = "global", string = org UUID
		const scope =
			values.organization_id === null ? "global" : values.organization_id;

		submitBusy.current = true;
		try {
			if (isEditing) {
				await updateTable.mutateAsync({
					params: {
						path: { table_id: table.id },
					},
					body: {
						description: values.description || null,
						schema: parsedSchema,
						policies,
					},
				});
			} else {
				await createTable.mutateAsync({
					params: {
						query: scope ? { scope } : undefined,
					},
					body: {
						name: values.name,
						description: values.description || null,
						schema: parsedSchema,
						policies,
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

	const isPending = createTable.isPending || updateTable.isPending;
	useEffect(() => {
		onBusyChange?.(isPending);
	}, [isPending, onBusyChange]);

	const saveError = form.formState.errors.root?.save?.message;
	useEffect(() => {
		if (saveError) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView({ block: "nearest" });
		}
	}, [saveError]);

	const title = isEditing ? "Edit Table" : "Create Table";
	const description = isEditing
		? "Update the table metadata"
		: "Create a new data table for storing documents";
	const content = (
		<>
			{isSolutionManaged && <SolutionManagedBanner entityLabel="table" />}

			<Form {...form}>
				<form
					onSubmit={(event) =>
						void form.handleSubmit(onSubmit)(event)
					}
					className="flex min-h-0 min-w-0 flex-1 flex-col"
				>
					<div
						role="region"
						aria-label="Table settings"
						className="min-h-0 min-w-0 flex-1 space-y-5 overflow-y-auto px-1 py-1"
					>
						{canChooseScope && (
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
												loadOrganizations={authorization.canAnywhere(
													"organizations.read",
												)}
												knownOrganizations={
													knownOrganizations
												}
												disabled={
													isEditing || isPending
												}
											/>
										</FormControl>
										<FormDescription>
											{isEditing
												? "Organization scope cannot be changed after a table is created."
												: "Global tables are available to all organizations."}
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
									<FormLabel>Table Name</FormLabel>
									<FormControl>
										<Input
											placeholder="my_table_name"
											disabled={isEditing || isPending}
											className="h-11 font-mono"
											{...field}
										/>
									</FormControl>
									<FormDescription>
										Start with a lowercase letter. Use
										lowercase letters, numbers, underscores,
										or hyphens.
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
											disabled={
												isPending || isSolutionManaged
											}
											placeholder="Describe the purpose of this table..."
											{...field}
										/>
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>

						<FormField
							control={form.control}
							name="schema"
							render={({ field }) => (
								<FormItem>
									<FormLabel>Schema (Optional)</FormLabel>
									<FormControl>
										<CodeEditor
											readOnly={
												isPending || isSolutionManaged
											}
											mode="json"
											text={field.value ?? ""}
											onChange={(next) =>
												field.onChange(next)
											}
											path="table-schema.json"
											height="200px"
											data-testid="table-schema-editor"
										/>
									</FormControl>
									<FormDescription>
										Optional JSON schema for validation
										hints
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>

						<div className="border-t pt-4">
							<PolicyEditor
								readOnly={isPending || isSolutionManaged}
								onParseErrorChange={setPolicyParseError}
								value={policies}
								onChange={setPolicies}
							/>
						</div>
					</div>
					<div className="shrink-0 space-y-4 border-t bg-background pt-4">
						{form.formState.errors.root?.save && (
							<Alert
								variant="destructive"
								ref={saveErrorRef}
								tabIndex={-1}
								className="max-h-36 overflow-y-auto outline-none"
							>
								<AlertTitle>
									Table could not be saved
								</AlertTitle>
								<AlertDescription>
									{form.formState.errors.root.save.message}
								</AlertDescription>
							</Alert>
						)}
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={isPending}
								onClick={onClose}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								className="min-h-11"
								disabled={
									isPending ||
									isSolutionManaged ||
									Boolean(policyParseError) ||
									!canManageCurrentScope ||
									!canManageSelectedScope
								}
							>
								{isPending
									? "Saving..."
									: isEditing
										? "Update"
										: "Create"}
							</Button>
						</DialogFooter>
					</div>
				</form>
			</Form>
		</>
	);

	if (embedded) {
		if (!open) return null;
		return (
			<section
				aria-labelledby="table-dialog-title"
				className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-card"
			>
				<div className="flex shrink-0 items-start gap-3 border-b border-border/70 bg-muted/20 px-4 py-3">
					<div className="flex size-9 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] border bg-background">
						<Table2 aria-hidden="true" className="size-4" />
					</div>
					<div className="min-w-0 flex-1">
						<h2
							id="table-dialog-title"
							className="text-base font-semibold leading-6"
						>
							{title}
						</h2>
						<p className="text-sm leading-5 text-muted-foreground">
							{description}
						</p>
					</div>
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label="Close table settings"
						disabled={isPending}
						onClick={onClose}
						className="size-9 shrink-0"
					>
						<X aria-hidden="true" className="size-4" />
					</Button>
				</div>
				<div className="flex min-h-0 flex-1 flex-col p-4">
					{content}
				</div>
			</section>
		);
	}

	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!nextOpen && !isPending) onClose();
			}}
		>
			<DialogContent
				className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[760px]"
				showCloseButton={!isPending}
				onEscapeKeyDown={(event) => {
					if (isPending) event.preventDefault();
				}}
				onInteractOutside={(event) => {
					if (isPending) event.preventDefault();
				}}
			>
				<DialogHeader>
					<DialogTitle>{title}</DialogTitle>
					<DialogDescription>{description}</DialogDescription>
				</DialogHeader>

				{content}
			</DialogContent>
		</Dialog>
	);
}
