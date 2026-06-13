import {
	AlertTriangle,
	Building2,
	FileCode,
	Globe,
	Pencil,
	PlayCircle,
	Trash2,
} from "lucide-react";

import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";

export type FormListItem = components["schemas"]["FormPublic"] & {
	missingRequiredParams?: string[];
	is_solution_managed?: boolean;
	solution_id?: string | null;
};

export interface FormValidationState {
	valid: boolean;
	missingParams: string[];
}

export interface FormListSurfaceProps {
	forms: FormListItem[];
	viewMode: "grid" | "table";
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	canManageForms: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	formValidation: Map<string, FormValidationState>;
	onLaunch: (form: FormListItem) => void;
	onEdit?: (form: FormListItem) => void;
	onDelete?: (form: FormListItem) => void;
	onToggleActive?: (form: FormListItem) => void;
	onCreateEmpty?: () => void;
	emptySearchActive?: boolean;
}

export function FormListSurface({
	forms,
	viewMode,
	isLoading = false,
	isPlatformAdmin,
	canManageForms,
	getOrgName,
	formValidation,
	onLaunch,
	onEdit,
	onDelete,
	onToggleActive,
	onCreateEmpty,
	emptySearchActive = false,
}: FormListSurfaceProps) {
	const terminology = useTerminology();

	if (isLoading) {
		return viewMode === "grid" || !canManageForms ? (
			<div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
				{[...Array(6)].map((_, i) => (
					<Skeleton key={i} className="h-48 w-full" />
				))}
			</div>
		) : (
			<div className="space-y-2">
				{[...Array(3)].map((_, i) => (
					<Skeleton key={i} className="h-12 w-full" />
				))}
			</div>
		);
	}

	if (forms.length === 0) {
		return (
			<Card>
				<CardContent className="flex flex-col items-center justify-center py-12 text-center">
					<FileCode className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						{emptySearchActive
							? `No ${term(terminology, "form", "pluralLower")} match your search`
							: `No ${term(terminology, "form", "pluralLower")} found`}
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						{emptySearchActive
							? "Try adjusting your search term or clear the filter"
							: canManageForms
								? `Get started by creating your first ${term(terminology, "form", "singularLower")}`
								: `No ${term(terminology, "form", "pluralLower")} are currently available`}
					</p>
					{canManageForms && !emptySearchActive && onCreateEmpty && (
						<Button
							variant="outline"
							size="icon"
							onClick={onCreateEmpty}
							className="mt-4"
							title={`Create ${term(terminology, "form", "singular")}`}
						>
							<FileCode className="h-4 w-4" />
						</Button>
					)}
				</CardContent>
			</Card>
		);
	}

	if (viewMode === "table" && canManageForms) {
		return (
			<div className="flex-1 min-h-0">
				<DataTable className="max-h-full">
					<DataTableHeader>
						<DataTableRow>
							{isPlatformAdmin && (
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
							)}
							<DataTableHead>Name</DataTableHead>
							<DataTableHead>Description</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap text-right" />
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{forms.map((form) => {
							const validation = formValidation.get(form.id);
							return (
								<DataTableRow key={form.id}>
									{isPlatformAdmin && (
										<DataTableCell className="w-0 whitespace-nowrap">
											{form.organization_id ? (
												<Badge variant="outline" className="text-xs">
													<Building2 className="mr-1 h-3 w-3" />
													{getOrgName(form.organization_id)}
												</Badge>
											) : (
												<Badge variant="default" className="text-xs">
													<Globe className="mr-1 h-3 w-3" />
													Global
												</Badge>
											)}
										</DataTableCell>
									)}
									<DataTableCell className="font-medium">
										{form.name}
									</DataTableCell>
									<DataTableCell className="max-w-xs truncate text-muted-foreground">
										{form.description || (
											<span className="italic">No description</span>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{canManageForms && onToggleActive ? (
											<Tooltip>
												<TooltipTrigger asChild>
													<div className="w-fit">
														<Switch
															checked={form.is_active}
															onCheckedChange={() => onToggleActive(form)}
															id={`form-active-table-${form.id}`}
														/>
													</div>
												</TooltipTrigger>
												<TooltipContent>
													{form.is_active
														? "Enabled - click to disable"
														: "Disabled - click to enable"}
												</TooltipContent>
											</Tooltip>
										) : (
											<Badge variant={form.is_active ? "default" : "secondary"}>
												{form.is_active ? "Enabled" : "Inactive"}
											</Badge>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<div className="flex gap-1 justify-end">
											<Button
												size="sm"
												onClick={() => onLaunch(form)}
												disabled={
													(!form.is_active && !canManageForms) ||
													!validation?.valid
												}
												title={
													!validation?.valid
														? `Cannot launch: Missing ${validation?.missingParams.join(", ")}`
														: !form.is_active && !canManageForms
															? `${term(terminology, "form", "singular")} is disabled`
															: `Launch ${term(terminology, "form", "singularLower")}`
												}
											>
												<PlayCircle className="h-4 w-4" />
											</Button>
											{form.is_solution_managed && (
												<SolutionManagedBadge solutionId={form.solution_id} />
											)}
											{canManageForms && !form.is_solution_managed && (
												<>
													{onEdit && (
														<Button
															variant="ghost"
															size="sm"
															onClick={() => onEdit(form)}
															title={`Edit ${term(terminology, "form", "singularLower")}`}
														>
															<Pencil className="h-4 w-4" />
														</Button>
													)}
													{onDelete && (
														<Button
															variant="ghost"
															size="sm"
															onClick={() => onDelete(form)}
															title={`Delete ${term(terminology, "form", "singularLower")}`}
														>
															<Trash2 className="h-4 w-4" />
														</Button>
													)}
												</>
											)}
										</div>
									</DataTableCell>
								</DataTableRow>
							);
						})}
					</DataTableBody>
				</DataTable>
			</div>
		);
	}

	return (
		<div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
			{forms.map((form) => {
				const validation = formValidation.get(form.id);
				return (
					<Card
						key={form.id}
						className="hover:border-primary transition-colors flex flex-col"
					>
						<CardHeader className="pb-3">
							<CardTitle className="text-base truncate" title={form.name}>
								{form.name}
							</CardTitle>
							{!validation?.valid && canManageForms && (
								<Badge variant="destructive" className="gap-1 w-fit mt-1">
									<AlertTriangle className="h-3 w-3" />
									Invalid
								</Badge>
							)}
							<CardDescription className="mt-1.5 text-sm line-clamp-2">
								{form.description || (
									<span className="italic text-muted-foreground/60">
										No description
									</span>
								)}
							</CardDescription>
						</CardHeader>
						<CardContent className="flex-1 flex flex-col pt-0">
							{!validation?.valid && canManageForms && (
								<div className="mb-3 pb-3 border-b">
									<span className="text-destructive font-medium text-sm">
										Missing required parameters:
									</span>
									<div className="mt-1.5 flex flex-wrap gap-1">
										{validation?.missingParams.map((param) => (
											<Badge
												key={param}
												variant="outline"
												className="text-xs font-mono"
											>
												{param}
											</Badge>
										))}
									</div>
								</div>
							)}

							<div className="flex-1" />

							{isPlatformAdmin && (
								<div className="mb-3">
									{form.organization_id ? (
										<Badge variant="outline" className="text-xs">
											<Building2 className="mr-1 h-3 w-3" />
											{getOrgName(form.organization_id)}
										</Badge>
									) : (
										<Badge variant="default" className="text-xs">
											<Globe className="mr-1 h-3 w-3" />
											Global
										</Badge>
									)}
								</div>
							)}

							<div className="flex items-center gap-2">
								<Button
									className="flex-1"
									onClick={() => onLaunch(form)}
									disabled={
										(!form.is_active && !canManageForms) || !validation?.valid
									}
									title={
										!validation?.valid
											? `Cannot launch: Missing required parameters (${validation?.missingParams.join(", ")})`
											: !form.is_active && !canManageForms
												? `${term(terminology, "form", "singular")} is disabled`
												: `Launch ${term(terminology, "form", "singularLower")}`
									}
								>
									<PlayCircle className="mr-2 h-4 w-4" />
									Launch
								</Button>
								{form.is_solution_managed && (
									<SolutionManagedBadge solutionId={form.solution_id} />
								)}
								{canManageForms && !form.is_solution_managed && (
									<>
										{onEdit && (
											<Button
												variant="outline"
												size="icon"
												onClick={() => onEdit(form)}
												title={`Edit ${term(terminology, "form", "singularLower")}`}
											>
												<Pencil className="h-4 w-4" />
											</Button>
										)}
										{onDelete && (
											<Button
												variant="outline"
												size="icon"
												onClick={() => onDelete(form)}
												title={`Delete ${term(terminology, "form", "singularLower")}`}
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										)}
										{onToggleActive && (
											<Tooltip>
												<TooltipTrigger asChild>
													<div className="shrink-0 ml-auto">
														<Switch
															checked={form.is_active}
															onCheckedChange={() => onToggleActive(form)}
															id={`form-active-${form.id}`}
														/>
													</div>
												</TooltipTrigger>
												<TooltipContent>
													{form.is_active
														? "Enabled - click to disable"
														: "Disabled - click to enable"}
												</TooltipContent>
											</Tooltip>
										)}
									</>
								)}
							</div>
						</CardContent>
					</Card>
				);
			})}
		</div>
	);
}
