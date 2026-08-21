import { useNavigate } from "react-router-dom";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { AlertCircle } from "lucide-react";
import { WorkersTab } from "./components/WorkersTab";
import { SchedulerTab } from "./components/SchedulerTab";
import { BuilderTab } from "./components/BuilderTab";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function DiagnosticsPage() {
	const { hasSelectedCapability, selectedTarget } = useAuthorizationBoundary();
	const navigate = useNavigate();

	if (
		!hasSelectedCapability("platformjobs.read") ||
		selectedTarget?.kind !== "platform"
	) {
		return (
			<div className="container mx-auto py-8">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						Select Global from Working in to view platform diagnostics.
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/")} className="mt-4">
					Return to Dashboard
				</Button>
			</div>
		);
	}

	return (
		<div className="h-full flex flex-col space-y-6">
			<div className="max-w-[1100px] mx-auto w-full">
				<h1 className="text-4xl font-extrabold tracking-tight">
					Diagnostics
				</h1>
				<p className="mt-2 text-muted-foreground">
					Monitor system health, process pools, and troubleshoot
					issues
				</p>
			</div>

			<Tabs
				defaultValue="workers"
				className="flex min-h-0 flex-1 flex-col"
			>
				<div className="max-w-[1100px] mx-auto w-full">
					<TabsList>
						<TabsTrigger value="workers">Workers</TabsTrigger>
						<TabsTrigger value="scheduler">Scheduler</TabsTrigger>
						<TabsTrigger value="builder">Builder</TabsTrigger>
					</TabsList>
				</div>
				<TabsContent
					value="workers"
					className="min-h-0 flex-1 overflow-auto pt-4"
				>
					<WorkersTab />
				</TabsContent>
				<TabsContent
					value="scheduler"
					className="min-h-0 flex-1 overflow-auto pt-4"
				>
					<SchedulerTab />
				</TabsContent>
				<TabsContent
					value="builder"
					className="min-h-0 flex-1 overflow-auto pt-4"
				>
					<BuilderTab />
				</TabsContent>
			</Tabs>
		</div>
	);
}

export default DiagnosticsPage;
