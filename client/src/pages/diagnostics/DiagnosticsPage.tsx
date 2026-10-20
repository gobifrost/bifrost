import { useNavigate } from "react-router-dom";
import { useAuthorization } from "@/services/authorization";
import { GLOBAL_TARGET } from "@/lib/authorization";
import { PageLoader } from "@/components/PageLoader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { AlertCircle } from "lucide-react";
import { WorkersTab } from "./components/WorkersTab";
import { SchedulerTab } from "./components/SchedulerTab";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function DiagnosticsPage() {
	const { canAt, isLoading } = useAuthorization();
	const navigate = useNavigate();

	if (isLoading) return <PageLoader message="Loading diagnostics…" />;
	if (
		!canAt("platform.read", GLOBAL_TARGET) ||
		!canAt("settings.read", GLOBAL_TARGET)
	) {
		return (
			<div className="container mx-auto py-8">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						You do not have permission to view diagnostics.
					</AlertDescription>
				</Alert>
				<Button
					type="button"
					onClick={() => navigate("/")}
					className="mt-4 min-h-11"
				>
					Return to Dashboard
				</Button>
			</div>
		);
	}

	return (
		<div className="mx-auto flex w-full min-w-0 max-w-[1100px] flex-col gap-6 lg:h-full lg:min-h-0">
			<ListPageHeader
				title="Diagnostics"
				description="Monitor system health, process pools, and troubleshoot issues"
			/>

			<Tabs
				defaultValue="workers"
				className="min-w-0 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col"
			>
				<div className="max-w-[1100px] mx-auto w-full shrink-0">
					<TabsList
						aria-label="Diagnostics views"
						className="w-full sm:w-auto"
					>
						<TabsTrigger className="min-h-11" value="workers">
							Workers
						</TabsTrigger>
						<TabsTrigger className="min-h-11" value="scheduler">
							Scheduler
						</TabsTrigger>
					</TabsList>
				</div>
				<TabsContent
					value="workers"
					className="min-w-0 pt-4 lg:min-h-0 lg:flex-1 lg:overflow-auto"
				>
					<WorkersTab />
				</TabsContent>
				<TabsContent
					value="scheduler"
					className="min-w-0 pt-4 lg:min-h-0 lg:flex-1 lg:overflow-auto"
				>
					<SchedulerTab />
				</TabsContent>
			</Tabs>
		</div>
	);
}

export default DiagnosticsPage;
