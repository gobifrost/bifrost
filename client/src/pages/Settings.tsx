import { useEffect, type ComponentType } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { WorkflowKeys } from "@/pages/settings/WorkflowKeys";
import { Branding } from "@/pages/settings/Branding";
import { OAuth } from "@/pages/settings/OAuth";
import { GitHub } from "@/pages/settings/GitHub";
import { LLMConfig } from "@/pages/settings/LLMConfig";
import { MCP } from "@/pages/settings/MCP";
import { Maintenance } from "@/pages/settings/Maintenance";
import { BuilderSettings } from "@/pages/settings/Builder";
import { Bot, Key, Palette, Plug, Shield, Sparkles, Wrench } from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";
import { cn } from "@/lib/utils";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";

interface SettingsContentProps {
	canWrite?: boolean;
	canExecute?: boolean;
}

const settingsTabs = [
	{ value: "ai", label: "AI", icon: Bot, readCapability: "configs.read", writeCapability: "configs.readwrite", content: LLMConfig },
	{ value: "builder", label: "Builder", icon: Sparkles, readCapability: "platformjobs.read", writeCapability: "platformjobs.execute", content: BuilderSettings },
	{ value: "mcp", label: "MCP", icon: Plug, readCapability: "integrations.read", writeCapability: "integrations.readwrite", content: MCP },
	{ value: "branding", label: "Branding", icon: Palette, readCapability: "configs.read", writeCapability: "configs.readwrite", content: Branding },
	{ value: "sso", label: "Authentication", icon: Shield, readCapability: "integrations.read", writeCapability: "integrations.readwrite", content: OAuth },
	{ value: "github", label: "GitHub", icon: Github, readCapability: "repository.read", writeCapability: "repository.readwrite", content: GitHub },
	{ value: "workflow-keys", label: "Workflow Keys", icon: Key, readCapability: "workflows.read", writeCapability: "workflows.readwrite", content: WorkflowKeys },
	{ value: "maintenance", label: "Maintenance", icon: Wrench, readCapability: "platformjobs.read", writeCapability: "platformjobs.execute", content: Maintenance },
] as const;

export function Settings() {
	const navigate = useNavigate();
	const location = useLocation();
	const { hasSelectedCapability } = useAuthorizationBoundary();
	const availableTabs = settingsTabs.filter((tab) =>
		hasSelectedCapability(tab.readCapability) ||
		hasSelectedCapability(tab.writeCapability),
	);

	// Parse the current tab from the URL path
	const requestedTab = location.pathname.split("/settings/")[1];
	const currentTab = availableTabs.some((tab) => tab.value === requestedTab)
		? requestedTab
		: availableTabs[0]?.value;

	const handleTabChange = (value: string) => {
		navigate(`/settings/${value}`);
	};

	// Keep the URL on the first setting the selected Role can actually manage.
	useEffect(() => {
		if (currentTab && location.pathname !== `/settings/${currentTab}`) {
			navigate(`/settings/${currentTab}`, { replace: true });
		}
	}, [currentTab, location.pathname, navigate]);

	if (!currentTab) return null;

	return (
		<div
			className={cn(
				"mx-auto flex h-full min-h-0 w-full flex-col space-y-6",
				currentTab === "builder" ? "max-w-6xl" : "max-w-3xl",
			)}
		>
			<div>
				<h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
					Settings
				</h1>
				<p className="mt-2 text-muted-foreground">
					Manage platform settings and configuration
				</p>
			</div>

			<Tabs
				value={currentTab}
				onValueChange={handleTabChange}
				className="flex min-h-0 flex-1 flex-col"
			>
				<div className="overflow-x-auto">
					<TabsList className="w-max">
						{availableTabs.map((tab) => {
							const Icon = tab.icon;
							return (
								<TabsTrigger key={tab.value} value={tab.value}>
									<Icon className="h-4 w-4 mr-1" />
									{tab.label}
								</TabsTrigger>
							);
						})}
					</TabsList>
				</div>

				{availableTabs.map((tab) => {
					const Content = tab.content as ComponentType<SettingsContentProps>;
					const canWrite = hasSelectedCapability(tab.writeCapability);
					return (
						<TabsContent
							key={tab.value}
							value={tab.value}
							className="mt-6 flex-1 min-h-0 overflow-auto"
						>
							<Content canWrite={canWrite} canExecute={canWrite} />
						</TabsContent>
					);
				})}
			</Tabs>
		</div>
	);
}
