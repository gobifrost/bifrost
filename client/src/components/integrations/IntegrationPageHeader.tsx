import { Link } from "react-router-dom";
import { Code, Download, Pencil, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";

export function IntegrationPageHeader({
	name,
	description,
	onTest,
	onGenerateSDK,
	onEdit,
	onExport,
	canExport,
	hasSetupNotes,
	showSetupNotes,
	onToggleSetupNotes,
	canTestConnection,
	canManageDefinitions,
}: {
	name: string;
	description?: string;
	onTest: () => void;
	onGenerateSDK: () => void;
	onEdit: () => void;
	onExport: () => void;
	canExport: boolean;
	hasSetupNotes: boolean;
	showSetupNotes: boolean;
	onToggleSetupNotes: () => void;
	canTestConnection: boolean;
	canManageDefinitions: boolean;
}) {
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
			</nav>
			<div className="flex min-w-0 flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
				<div className="min-w-0">
					<h1 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
						{name}
					</h1>
					<p className="mt-2 max-w-2xl text-sm text-muted-foreground [overflow-wrap:anywhere]">
						{description ||
							"Configure OAuth, data providers, and organization mappings"}
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-2 xl:shrink-0">
					{hasSetupNotes && (
						<Button type="button" variant="outline" className="min-h-11" onClick={onToggleSetupNotes}>
							{showSetupNotes ? "Hide Setup Instructions" : "Show Setup Instructions"}
						</Button>
					)}
					{canExport && (
						<Button type="button" variant="outline" className="min-h-11" onClick={onExport}>
							<Download className="size-4" />
							Export
						</Button>
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
							onClick={onGenerateSDK}
						>
							<Code className="size-4" />
							Generate SDK
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
							Edit
						</Button>
					)}
				</div>
			</div>
		</header>
	);
}
