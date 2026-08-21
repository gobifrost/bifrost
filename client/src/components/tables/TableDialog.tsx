import { useEffect, useState } from "react";
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
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";
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
}

export function TableDialog({ table, open, onClose }: TableDialogProps) {
	const createTable = useCreateTable();
	const updateTable = useUpdateTable();
	const { selectedTarget, hasSelectedCapability } = useAuthorizationBoundary();
	const isEditing = !!table;
	// Solution-managed tables are read-only on the platform: deploy owns schema +
	// policies (criterion 6). Row data stays editable elsewhere (criterion 7).
	const isSolutionManaged = table?.is_solution_managed ?? false;

	const defaultOrgId =
		selectedTarget?.kind === "platform"
			? null
			: (selectedTarget?.organization_id ?? null);
	const canManageTables =
		selectedTarget?.kind !== "managed_organizations" &&
		hasSelectedCapability("tables.readwrite");

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
	// Track the entity identity we last initialized policies from so we can
	// reset render-phase (rather than in an effect) when the dialog is
	// reopened for a different table.
	const [lastPolicyKey, setLastPolicyKey] = useState<string>(
		table?.id ?? "__new__",
	);
	const currentPolicyKey = open ? (table?.id ?? "__new__") : lastPolicyKey;
	if (currentPolicyKey !== lastPolicyKey) {
		setLastPolicyKey(currentPolicyKey);
		setPolicies(table?.policies ?? null);
	}

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
	};

	const isPending = createTable.isPending || updateTable.isPending;

	return (
		<Dialog open={open} onOpenChange={onClose}>
			<DialogContent className="sm:max-w-[760px] max-h-[90vh] overflow-y-auto">
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

				{isSolutionManaged && <SolutionManagedBanner entityLabel="table" />}

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
										<CodeEditor
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
								value={policies}
								onChange={setPolicies}
							/>
						</div>

						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={onClose}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								disabled={
									isPending ||
									isSolutionManaged ||
									!canManageTables
								}
							>
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
