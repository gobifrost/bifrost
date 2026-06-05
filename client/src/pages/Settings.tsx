import { useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { WorkflowKeys } from "@/pages/settings/WorkflowKeys";
import { Branding } from "@/pages/settings/Branding";
import { OAuth } from "@/pages/settings/OAuth";
import { GitHub } from "@/pages/settings/GitHub";
import { LLMConfig } from "@/pages/settings/LLMConfig";
import { MCP } from "@/pages/settings/MCP";
import { Maintenance } from "@/pages/settings/Maintenance";
import { Bot, Key, Palette, Plug, Shield, Wrench } from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";

export function Settings() {
	const navigate = useNavigate();
	const location = useLocation();

	// Parse the current tab from the URL path
	const currentTab = location.pathname.split("/settings/")[1] || "ai";

	const handleTabChange = (value: string) => {
		navigate(`/settings/${value}`);
	};

	// Redirect /settings to /settings/ai (first tab)
	useEffect(() => {
		if (location.pathname === "/settings") {
			navigate("/settings/ai", { replace: true });
		}
	}, [location.pathname, navigate]);

	return (
		<div className="flex h-full min-h-0 flex-col space-y-6 mx-auto max-w-3xl">
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
						<TabsTrigger value="ai">
							<Bot className="h-4 w-4 mr-1" />
							AI
						</TabsTrigger>
						<TabsTrigger value="mcp">
							<Plug className="h-4 w-4 mr-1" />
							MCP
						</TabsTrigger>
						<TabsTrigger value="branding">
							<Palette className="h-4 w-4 mr-1" />
							Branding
						</TabsTrigger>
						<TabsTrigger value="sso">
							<Shield className="h-4 w-4 mr-1" />
							SSO
						</TabsTrigger>
						<TabsTrigger value="github">
							<Github className="h-4 w-4 mr-1" />
							GitHub
						</TabsTrigger>
						<TabsTrigger value="workflow-keys">
							<Key className="h-4 w-4 mr-1" />
							Workflow Keys
						</TabsTrigger>
						<TabsTrigger value="maintenance">
							<Wrench className="h-4 w-4 mr-1" />
							Maintenance
						</TabsTrigger>
					</TabsList>
				</div>

				<TabsContent value="ai" className="mt-6 flex-1 min-h-0 overflow-auto">
					<LLMConfig />
				</TabsContent>

				<TabsContent value="mcp" className="mt-6 flex-1 min-h-0 overflow-auto">
					<MCP />
				</TabsContent>

				<TabsContent value="branding" className="mt-6 flex-1 min-h-0 overflow-auto">
					<Branding />
				</TabsContent>

				<TabsContent value="sso" className="mt-6 flex-1 min-h-0 overflow-auto">
					<OAuth />
				</TabsContent>

				<TabsContent value="github" className="mt-6 flex-1 min-h-0 overflow-auto">
					<GitHub />
				</TabsContent>

				<TabsContent value="workflow-keys" className="mt-6 flex-1 min-h-0 overflow-auto">
					<WorkflowKeys />
				</TabsContent>

				<TabsContent value="maintenance" className="mt-6 flex-1 min-h-0 overflow-auto">
					<Maintenance />
				</TabsContent>
			</Tabs>
		</div>
	);
}
