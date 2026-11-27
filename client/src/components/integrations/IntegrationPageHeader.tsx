import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
	ArrowLeft,
	BookOpen,
	Code,
	Download,
	MoreHorizontal,
	Pencil,
	Plus,
	Settings,
	Zap,
} from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function IntegrationPageHeader({
	name,
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
}: {
	name: string;
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
	/** Status for the selected connection. */
	status?: ReactNode;
	workflowActions?: ReactNode;
	/** Connection-instance selector, kept in the detail breadcrumb. */
	instanceSelector?: ReactNode;
}) {
	const canAddSetupNotes = !hasSetupNotes && canManageDefinitions;
	const hasMoreActions =
		canTestConnection || hasSetupNotes || canAddSetupNotes || canExport || canManageDefinitions;
	return (
		<header className="flex min-w-0 items-center gap-2 sm:gap-4">
			<div className="flex min-w-0 items-center gap-1 sm:gap-2">
				<nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 sm:gap-2 text-sm text-muted-foreground">
					<Link to="/integrations" aria-label="Back to Integrations" className="inline-flex size-8 shrink-0 items-center justify-center text-primary hover:underline sm:w-auto">
						<ArrowLeft className="size-4 sm:hidden" />
						<span className="hidden sm:inline">Integrations</span>
					</Link>
					<span aria-hidden="true" className="hidden sm:inline">/</span>
					<h1 className="min-w-0 truncate text-base font-semibold tracking-tight text-foreground sm:text-xl">
						{name}
					</h1>
					{instanceSelector && <><span aria-hidden="true">/</span>{instanceSelector}</>}
				</nav>
				{canManageDefinitions && onConfigureInstance && (
					<Tooltip>
						<TooltipTrigger asChild>
							<Button type="button" variant="ghost" size="icon-sm" className="size-8" onClick={onConfigureInstance} aria-label="Configure Instance">
								<Settings className="size-4" />
							</Button>
						</TooltipTrigger>
						<TooltipContent>Configure Instance</TooltipContent>
					</Tooltip>
				)}
			</div>
			{status}
			<div className="ml-auto flex shrink-0 items-center gap-2">
				{workflowActions}
					{hasMoreActions && (
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<Button
									type="button"
									variant="outline"
									size="icon-sm"
									className="size-8"
									aria-label="More integration actions"
								>
									<MoreHorizontal className="size-4" />
								</Button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="end">
								{canTestConnection && (
									<DropdownMenuItem onClick={onTest}><Zap />Test Connection</DropdownMenuItem>
								)}
								{canManageDefinitions && (
									<DropdownMenuItem onClick={onEdit}><Pencil />Edit Integration</DropdownMenuItem>
								)}
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
		</header>
	);
}
