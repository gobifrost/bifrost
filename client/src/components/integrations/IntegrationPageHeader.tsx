import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
	BookOpen,
	Code,
	Download,
	MoreHorizontal,
	Pencil,
	Plus,
	Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function IntegrationPageHeader({
	name,
	description,
	onTest,
	onGenerateSDK,
	onEdit,
	onConfigureInstance,
	onExport,
	canExport,
	hasSetupNotes,
	showSetupNotes,
	onToggleSetupNotes,
	onAddSetupNotes,
	canTestConnection,
	canManageDefinitions,
	status,
	workflowActions,
	instanceSelector,
	sharedDefinitionDescription,
}: {
	name: string;
	description?: string;
	onTest: () => void;
	onGenerateSDK: () => void;
	onEdit: () => void;
	onConfigureInstance?: () => void;
	onExport: () => void;
	canExport: boolean;
	hasSetupNotes: boolean;
	showSetupNotes: boolean;
	onToggleSetupNotes: () => void;
	onAddSetupNotes: () => void;
	canTestConnection: boolean;
	canManageDefinitions: boolean;
	/** Status chips shown under the name. */
	status?: ReactNode;
	workflowActions?: ReactNode;
	/** Connection-instance selector, kept in the detail breadcrumb. */
	instanceSelector?: ReactNode;
	/** Explains the scope of definition edits when an integration has instances. */
	sharedDefinitionDescription?: string;
}) {
	const canAddSetupNotes = !hasSetupNotes && canManageDefinitions;
	const hasMoreActions =
		hasSetupNotes || canAddSetupNotes || canExport || canManageDefinitions;
	return (
		<header className="min-w-0 space-y-3">
			<nav
				aria-label="Breadcrumb"
				className="flex min-w-0 flex-wrap items-center gap-2 text-sm text-muted-foreground"
			>
				<Link
					to="/integrations"
					className="inline-flex min-h-11 items-center text-primary hover:underline"
				>
					Integrations
				</Link>
				<span aria-hidden="true">/</span>
				<span aria-current="page" className="[overflow-wrap:anywhere]">
					{name}
				</span>
				{instanceSelector && (
					<>
						<span aria-hidden="true">/</span>
						{instanceSelector}
					</>
				)}
			</nav>
			<div className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
				<div className="min-w-0 space-y-3">
					<div>
						<h1 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
							{name}
						</h1>
						{description && (
							<p className="mt-1 max-w-2xl text-sm text-muted-foreground [overflow-wrap:anywhere]">
								{description}
							</p>
						)}
						{sharedDefinitionDescription && (
							<p className="mt-1 text-sm text-muted-foreground">
								{sharedDefinitionDescription}
							</p>
						)}
					</div>
					{status}
				</div>
				<div
					className={
						workflowActions
							? "flex w-full min-w-0 flex-wrap items-center gap-2 lg:w-[65%] lg:justify-end"
							: "flex flex-wrap items-center gap-2 lg:shrink-0"
					}
				>
					{workflowActions}
					{workflowActions && canTestConnection && (
						<span
							role="separator"
							aria-orientation="vertical"
							className="hidden h-6 w-px shrink-0 bg-border sm:block"
						/>
					)}
					{canTestConnection && (
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							onClick={onTest}
						>
							<Zap className="size-4" />
							Test Connection
						</Button>
					)}
					{canManageDefinitions && (
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							onClick={onConfigureInstance}
						>
							Configure Instance
						</Button>
					)}
					{canManageDefinitions && (
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							onClick={onEdit}
						>
							<Pencil className="size-4" />
							Edit Shared Definition
						</Button>
					)}
					{hasMoreActions && (
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<Button
									type="button"
									variant="outline"
									size="icon"
									className="size-11"
									aria-label="More integration actions"
								>
									<MoreHorizontal className="size-4" />
								</Button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="end">
								{hasSetupNotes && (
									<DropdownMenuItem
										onClick={onToggleSetupNotes}
									>
										<BookOpen />
										{showSetupNotes
											? "Hide Setup Instructions"
											: "Show Setup Instructions"}
									</DropdownMenuItem>
								)}
								{canAddSetupNotes && (
									<DropdownMenuItem onClick={onAddSetupNotes}>
										<Plus />
										Add Setup Instructions
									</DropdownMenuItem>
								)}
								{canExport && (
									<DropdownMenuItem onClick={onExport}>
										<Download />
										Export
									</DropdownMenuItem>
								)}
								{canManageDefinitions && (
									<DropdownMenuItem onClick={onGenerateSDK}>
										<Code />
										Generate SDK
									</DropdownMenuItem>
								)}
							</DropdownMenuContent>
						</DropdownMenu>
					)}
				</div>
			</div>
		</header>
	);
}
