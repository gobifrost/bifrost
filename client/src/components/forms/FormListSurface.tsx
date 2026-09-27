import {
	AlertTriangle,
	Building2,
	FileCode,
	Globe,
	MoreVertical,
	Pencil,
	Power,
	Share2,
	Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";

import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { ResourceCatalogCard } from "@/components/catalog/ResourceCatalogCard";
import { ResourceIcon } from "@/components/ResourceIcon";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
	/** Query string to preserve the originating page in native navigation links. */
	navigationSearch?: string;
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	canManageForms: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	formValidation: Map<string, FormValidationState>;
	onLaunch: (form: FormListItem) => void;
	onShare?: (form: FormListItem) => void;
	onEdit?: (form: FormListItem) => void;
	onDelete?: (form: FormListItem) => void;
	onToggleActive?: (form: FormListItem) => void;
	onCreateEmpty?: () => void;
	emptySearchActive?: boolean;
}

export function FormListSurface({
	forms,
	viewMode,
	navigationSearch = "",
	isLoading = false,
	isPlatformAdmin,
	canManageForms,
	getOrgName,
	formValidation,
	onLaunch,
	onShare,
	onEdit,
	onDelete,
	onToggleActive,
	onCreateEmpty,
	emptySearchActive = false,
}: FormListSurfaceProps) {
	const terminology = useTerminology();

	if (isLoading) {
		return viewMode === "grid" || !canManageForms ? (
			<div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,280px),1fr))]">
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
							size="icon-lg"
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
							<DataTableHead className="w-0 whitespace-nowrap">
								Status
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap text-right" />
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{forms.map((form) => {
							const validation = formValidation.get(form.id);
							const launchHref = `/execute/${form.id}${navigationSearch}`;
							const canLaunch =
								(form.is_active || canManageForms) &&
								validation?.valid;
							const unavailableReason = !validation?.valid
								? `Cannot launch: Missing ${validation?.missingParams.join(", ")}`
								: !form.is_active && !canManageForms
									? `${term(terminology, "form", "singular")} is disabled`
									: undefined;
							return (
								<DataTableRow
									key={form.id}
									clickable={canLaunch}
									href={canLaunch ? launchHref : undefined}
									onClick={
										canLaunch
											? () => onLaunch(form)
											: undefined
									}
								>
									{isPlatformAdmin && (
										<DataTableCell className="w-0 whitespace-nowrap">
											{form.organization_id ? (
												<Badge
													variant="outline"
													className="text-xs"
												>
													<Building2 className="mr-1 h-3 w-3" />
													{getOrgName(
														form.organization_id,
													)}
												</Badge>
											) : (
												<Badge
													variant="outline"
													className="text-xs"
												>
													<Globe className="mr-1 h-3 w-3" />
													Global
												</Badge>
											)}
										</DataTableCell>
									)}
									<DataTableCell className="font-medium">
										<div className="flex min-w-0 items-center gap-2">
											<ResourceIcon
												kind="form"
												id={form.id}
												logo={form.logo_url ?? null}
												cacheKey={
													form.logo_version ??
													undefined
												}
												size="table"
											/>
											{canLaunch ? (
												<Link
													to={launchHref}
													className="min-w-0 [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
												>
													{form.name}
												</Link>
											) : (
												<span className="min-w-0 [overflow-wrap:anywhere]">
													<span
														title={
															unavailableReason
														}
													>
														{form.name}
													</span>
												</span>
											)}
										</div>
									</DataTableCell>
									<DataTableCell className="max-w-xs truncate text-muted-foreground">
										{form.description || (
											<span className="italic">
												No description
											</span>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{canManageForms && onToggleActive ? (
											<Tooltip>
												<TooltipTrigger asChild>
													<div
														className="w-fit"
														onClick={(event) =>
															event.stopPropagation()
														}
													>
														<Switch
															checked={
																form.is_active
															}
															onCheckedChange={() =>
																onToggleActive(
																	form,
																)
															}
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
											<Badge
												variant={
													form.is_active
														? "outline"
														: "secondary"
												}
											>
												{form.is_active
													? "Enabled"
													: "Inactive"}
											</Badge>
										)}
									</DataTableCell>
									<DataTableCell
										className="w-0 whitespace-nowrap text-right"
										onClick={(event) =>
											event.stopPropagation()
										}
									>
										<div className="flex gap-1 justify-end">
											{form.is_solution_managed && (
												<SolutionManagedBadge
													solutionId={
														form.solution_id
													}
												/>
											)}
											{canManageForms &&
												(onShare ||
													onEdit ||
													onDelete) && (
													<DropdownMenu>
														<DropdownMenuTrigger
															asChild
														>
															<Button
																variant="ghost"
																size="icon-lg"
																aria-label={`${form.name} actions`}
															>
																<MoreVertical className="h-4 w-4" />
															</Button>
														</DropdownMenuTrigger>
														<DropdownMenuContent
															align="end"
															className="w-48"
														>
															{onShare && (
																<DropdownMenuItem
																	className="min-h-11 whitespace-nowrap px-3"
																	onClick={() =>
																		onShare(
																			form,
																		)
																	}
																>
																	<Share2 />{" "}
																	Share Form
																</DropdownMenuItem>
															)}
															{!form.is_solution_managed &&
																onEdit && (
																	<DropdownMenuItem
																		className="min-h-11 whitespace-nowrap px-3"
																		onClick={() =>
																			onEdit(
																				form,
																			)
																		}
																	>
																		<Pencil />{" "}
																		Edit
																		Form
																	</DropdownMenuItem>
																)}
															{!form.is_solution_managed &&
																onDelete && (
																	<>
																		<DropdownMenuSeparator />
																		<DropdownMenuItem
																			variant="destructive"
																			className="min-h-11 whitespace-nowrap px-3"
																			onClick={() =>
																				onDelete(
																					form,
																				)
																			}
																		>
																			<Trash2 />{" "}
																			Delete
																			Form
																		</DropdownMenuItem>
																	</>
																)}
														</DropdownMenuContent>
													</DropdownMenu>
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
		<div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,280px),1fr))]">
			{forms.map((form) => {
				const validation = formValidation.get(form.id);
				const launchHref = `/execute/${form.id}${navigationSearch}`;
				const canLaunch =
					(form.is_active || canManageForms) && validation?.valid;
				return (
					<ResourceCatalogCard
						key={form.id}
						icon={
							<ResourceIcon
								kind="form"
								id={form.id}
								logo={form.logo_url ?? null}
								cacheKey={form.logo_version ?? undefined}
								size="card"
								className="border-amber-500/20 bg-amber-500/10 text-amber-800 dark:text-amber-300 [&_svg]:text-current"
							/>
						}
						title={form.name}
						subtitle={
							<>
								{term(terminology, "form", "singular")}
								<span> · </span>
								{form.is_active ? "Enabled" : "Disabled"}
							</>
						}
						description={
							form.description || (
								<span className="italic text-muted-foreground/60">
									No description
								</span>
							)
						}
						action={
							<div className="flex items-center gap-1">
								{form.is_solution_managed && (
									<SolutionManagedBadge
										solutionId={form.solution_id}
									/>
								)}
								{canManageForms &&
									(onShare ||
										onEdit ||
										onDelete ||
										onToggleActive) && (
										<RecordActionsMenu
											label={`${form.name} actions`}
											contentClassName="w-48"
										>
											{onShare && (
												<DropdownMenuItem
													className="min-h-11 whitespace-nowrap px-3"
													onSelect={() =>
														onShare(form)
													}
												>
													<Share2 /> Share Form
												</DropdownMenuItem>
											)}
											{!form.is_solution_managed &&
												onEdit && (
													<DropdownMenuItem
														className="min-h-11 whitespace-nowrap px-3"
														onSelect={() =>
															onEdit(form)
														}
													>
														<Pencil /> Edit Form
													</DropdownMenuItem>
												)}
											{!form.is_solution_managed &&
												onToggleActive && (
													<DropdownMenuItem
														className="min-h-11 whitespace-nowrap px-3"
														onSelect={() =>
															onToggleActive(form)
														}
													>
														<Power />{" "}
														{form.is_active
															? "Disable Form"
															: "Enable Form"}
													</DropdownMenuItem>
												)}
											{!form.is_solution_managed &&
												onDelete && (
													<>
														<DropdownMenuSeparator />
														<DropdownMenuItem
															variant="destructive"
															className="min-h-11 whitespace-nowrap px-3"
															onSelect={() =>
																onDelete(form)
															}
														>
															<Trash2 /> Delete
															Form
														</DropdownMenuItem>
													</>
												)}
										</RecordActionsMenu>
									)}
							</div>
						}
						footer={
							isPlatformAdmin ? (
								<p className="flex items-center gap-2">
									{form.organization_id ? (
										<>
											<Building2 className="size-3.5 shrink-0" />
											<span className="truncate">
												{getOrgName(
													form.organization_id,
												)}
											</span>
										</>
									) : (
										<>
											<Globe className="size-3.5 shrink-0" />
											<span className="truncate">
												Global
											</span>
										</>
									)}
								</p>
							) : undefined
						}
						onOpen={() => onLaunch(form)}
						href={canLaunch ? launchHref : undefined}
						disabled={!canLaunch}
					>
						{!validation?.valid && canManageForms && (
							<div className="space-y-3 border-t pt-3">
								<Badge variant="destructive" className="gap-1">
									<AlertTriangle className="h-3 w-3" />
									Invalid
								</Badge>
								<div>
									<span className="text-destructive font-medium text-sm">
										Missing required parameters:
									</span>
									<div className="mt-1.5 flex flex-wrap gap-1">
										{validation?.missingParams.map(
											(param) => (
												<Badge
													key={param}
													variant="outline"
													className="text-xs font-mono"
												>
													{param}
												</Badge>
											),
										)}
									</div>
								</div>
							</div>
						)}
					</ResourceCatalogCard>
				);
			})}
		</div>
	);
}
