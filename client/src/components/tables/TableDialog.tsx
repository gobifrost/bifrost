import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import { useCreateTable, useUpdateTable } from "@/services/tables";
import type { TablePublic } from "@/services/tables";
import { TableAccessEditor } from "@/components/tables/TableAccessEditor";
import { useRoles } from "@/hooks/useRoles";
import type { components } from "@/lib/v1";

type TableAccess = components["schemas"]["TableAccess"];

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
}

export function TableDialog({ table, open, onClose }: TableDialogProps) {
	const createTable = useCreateTable();
	const updateTable = useUpdateTable();
	const { isPlatformAdmin, user } = useAuth();
	const { data: rolesData } = useRoles();
	const isEditing = !!table;

	// Access state is re-derived each time the dialog opens (via key on inner component).
	// We track the "last opened" table to detect when to reset.
	const lastTableRef = useRef<typeof table>(undefined);
	const lastOpenRef = useRef<boolean>(false);
	const [accessExpanded, setAccessExpanded] = useState(() => !!table?.access);
	const [access, setAccess] = useState<TableAccess | null>(
		() => table?.access ?? null,
	);

	// Derive original organization_id from the table prop
	const originalOrgId = useMemo(() => table?.organization_id ?? null, [table]);

	// Default organization_id for org users is their org, for platform admins it's null (global)
	const defaultOrgId = isPlatformAdmin
		? null
		: (user?.organizationId ?? null);

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			name: "",
			description: "",
			schema: "",
			organization_id: defaultOrgId,
		},
	});

	// Watch organization_id to detect scope changes
	const watchedOrgId = useWatch({ control: form.control, name: "organization_id" });
	const scopeChanged = isEditing && watchedOrgId !== originalOrgId;

	const resetAccessState = useCallback(() => {
		setAccess(table?.access ?? null);
		setAccessExpanded(!!table?.access);
	}, [table]);

	useEffect(() => {
		const tableChanged = lastTableRef.current !== table;
		const justOpened = open && !lastOpenRef.current;
		lastTableRef.current = table;
		lastOpenRef.current = open;

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

		if (tableChanged || justOpened) {
			resetAccessState();
		}
	}, [table, form, open, defaultOrgId, resetAccessState]);

	const onSubmit = async (values: FormValues) => {
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

		if (isEditing) {
			await updateTable.mutateAsync({
				params: {
					path: { table_id: table.id },
				},
				body: {
					description: values.description || null,
					schema: parsedSchema,
					access: access,
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
					access: access,
				},
			});
		}
		onClose();
	};

	const isPending = createTable.isPending || updateTable.isPending;

	return (
		<Dialog open={open} onOpenChange={onClose}>
			<DialogContent className="sm:max-w-[500px]">
				<DialogHeader>
					<DialogTitle>
						{isEditing ? "Edit Table" : "Create Table"}
					</DialogTitle>
					<DialogDescription>
						{isEditing
							? "Update the table metadata"
							: "Create a new data table for storing documents"}
					</DialogDescription>
				</DialogHeader>

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
											Global tables are available to all
											organizations
										</FormDescription>
										<FormMessage />
										{scopeChanged && (
											<Alert className="mt-2 bg-amber-50 border-amber-200 dark:bg-amber-950 dark:border-amber-800">
												<AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
												<AlertDescription className="text-amber-800 dark:text-amber-200">
													Changing table scope affects
													which users can access this
													data. Existing records will
													remain but may become
													visible/hidden to different
													users.
												</AlertDescription>
											</Alert>
										)}
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
											disabled={isEditing}
											{...field}
										/>
									</FormControl>
									<FormDescription>
										Lowercase letters, numbers, and
										underscores only
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
										<Textarea
											placeholder='{"type": "object", "properties": {...}}'
											className="font-mono text-sm"
											rows={5}
											{...field}
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

						{/* Access Rules — collapsed by default (opt-in) */}
						<div className="border rounded-md">
							<button
								type="button"
								className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium hover:bg-muted/50 transition-colors"
								onClick={() => setAccessExpanded((v) => !v)}
								aria-expanded={accessExpanded}
							>
								<span>
									Access Rules{" "}
									<span className="text-muted-foreground font-normal">
										(advanced)
									</span>
								</span>
								{accessExpanded ? (
									<ChevronDown className="h-4 w-4 text-muted-foreground" />
								) : (
									<ChevronRight className="h-4 w-4 text-muted-foreground" />
								)}
							</button>
							{accessExpanded && (
								<div className="px-4 pb-4">
									<p className="text-xs text-muted-foreground mb-3">
										Configure who can read, create, update, or
										delete documents. Leave unset to restrict
										access to workflows only.
									</p>
									<TableAccessEditor
										value={access}
										roles={
											rolesData?.map((r) => ({
												id: r.id,
												name: r.name,
											})) ?? []
										}
										onChange={setAccess}
									/>
								</div>
							)}
						</div>

						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={onClose}
							>
								Cancel
							</Button>
							<Button type="submit" disabled={isPending}>
								{isPending
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
