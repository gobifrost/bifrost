import { NavLink } from "react-router-dom";
import {
	LayoutDashboard,
	Workflow,
	History,
	Building,
	Users,
	FileCode,
	Key,
	UserCog,
	Settings as SettingsIcon,
	X,
	Stethoscope,
	ShieldCheck,
	MessageSquare,
	Bot,
	Plug,
	DollarSign,
	Activity,
	Webhook,
	Database,
	FolderOpen,
	AppWindow,
	Network,
	BookOpen,
	ServerCog,
	Boxes,
	FileCheck2,
	Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/contexts/AuthContext";
import { Logo } from "@/components/branding/Logo";
import { Button } from "@/components/ui/button";
import { term, useTerminology, type ProductTermKey } from "@/lib/terminology";
import { useBuilderAccess } from "@/hooks/useBuilderAccess";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";

interface NavItem {
	title: string;
	termKey?: ProductTermKey;
	href: string;
	icon: React.ElementType;
	requiresPlatformAdmin?: boolean;
	requiresBuilder?: boolean;
	requiredCapability?: string;
	requiredAnyCapabilities?: string[];
	requiredBoundaryKind?: "platform";
	dividerBefore?: boolean;
}

interface NavSection {
	title: string;
	items: NavItem[];
	requiresPlatformAdmin?: boolean;
}

const navSections: NavSection[] = [
	{
		title: "Overview",
		requiresPlatformAdmin: true,
		items: [
			{
				title: "Dashboard",
				href: "/",
				icon: LayoutDashboard,
				requiresPlatformAdmin: true,
			},
		],
	},
	{
		title: "Hub",
		items: [
			{
				title: "Build",
				href: "/build",
				icon: Sparkles,
				requiresBuilder: true,
			},
			{
				title: "Chat",
				href: "/chat",
				icon: MessageSquare,
			},
			{
				title: "Apps",
				termKey: "app",
				href: "/apps",
				icon: AppWindow,
				requiredCapability: "apps.read",
			},
			{
				title: "Forms",
				termKey: "form",
				href: "/forms",
				icon: FileCode,
				requiredCapability: "forms.read",
			},
			{
				title: "History",
				href: "/history",
				icon: History,
				requiredCapability: "executions.read",
			},
		],
	},
	{
		title: "Automation",
		items: [
			{
				title: "Agents",
				termKey: "agent",
				href: "/agents",
				icon: Bot,
				requiredCapability: "agents.read",
			},
			{
				title: "Workflows",
				href: "/workflows",
				icon: Workflow,
				requiredCapability: "workflows.read",
			},
		],
	},
	{
		title: "Data",
		items: [
			{
				title: "Config",
				href: "/config",
				icon: Key,
				requiredCapability: "configs.read",
			},
			{
				title: "Tables",
				href: "/tables",
				icon: Database,
				requiredCapability: "tables.read",
			},
			{
				title: "Files",
				href: "/files",
				icon: FolderOpen,
				requiredCapability: "managedfiles.read",
			},
			{
				title: "Knowledge",
				href: "/knowledge",
				icon: BookOpen,
				requiredCapability: "knowledge.read",
			},
			{
				title: "Integrations",
				href: "/integrations",
				icon: Plug,
				requiredCapability: "integrations.read",
			},
			{
				title: "MCP Servers",
				href: "/mcp-servers",
				icon: ServerCog,
				requiredCapability: "integrations.read",
			},
			{
				title: "Events",
				href: "/event-sources",
				icon: Webhook,
				requiredCapability: "events.read",
			},
			{
				title: "Entity Management",
				href: "/entity-management",
				icon: Network,
				requiresPlatformAdmin: true,
			},
		],
	},
	{
		title: "Platform",
		items: [
			{
				title: "Organizations",
				href: "/organizations",
				icon: Building,
				requiredCapability: "organizations.read",
			},
			{
				title: "Users",
				href: "/users",
				icon: Users,
				requiredCapability: "organizations.read",
			},
			{
				title: "Roles",
				href: "/roles",
				icon: UserCog,
				requiredCapability: "roles.read",
			},
			{
				title: "Solutions",
				href: "/solutions",
				icon: Boxes,
				requiredCapability: "solutions.read",
			},
			{
				title: "Promotion review",
				href: "/solution-promotions",
				icon: FileCheck2,
				requiredCapability: "solutions.publish.read",
			},
			{
				title: "Settings",
				href: "/settings",
				icon: SettingsIcon,
				requiredAnyCapabilities: [
					"configs.read",
					"configs.readwrite",
					"integrations.read",
					"integrations.readwrite",
					"platformjobs.read",
					"platformjobs.execute",
					"repository.read",
					"repository.readwrite",
					"workflows.read",
					"workflows.readwrite",
				],
				requiredBoundaryKind: "platform",
			},
			{
				title: "Diagnostics",
				href: "/diagnostics",
				icon: Stethoscope,
				requiredCapability: "platformjobs.read",
				requiredBoundaryKind: "platform",
			},
			{
				title: "Audit Log",
				href: "/audit",
				icon: ShieldCheck,
				requiredCapability: "audit.read",
				requiredBoundaryKind: "platform",
			},
		],
	},
	{
		title: "Reports",
		items: [
			{
				title: "ROI",
				href: "/reports/roi",
				icon: DollarSign,
				requiredCapability: "metrics.read",
				requiredBoundaryKind: "platform",
			},
			{
				title: "Usage",
				href: "/reports/usage",
				icon: Activity,
				requiredCapability: "metrics.read",
			},
		],
	},
];

interface SidebarProps {
	isMobileMenuOpen: boolean;
	setIsMobileMenuOpen: (open: boolean) => void;
	isCollapsed: boolean;
}

export function Sidebar({
	isMobileMenuOpen,
	setIsMobileMenuOpen,
	isCollapsed,
}: SidebarProps) {
	const { isPlatformAdmin } = useAuth();
	const { hasSelectedCapability, selectedTarget } = useAuthorizationBoundary();
	const terminology = useTerminology();
	const { canAccessBuilder } = useBuilderAccess();

	// Filter sections and items based on user permissions
	const visibleSections = navSections
		.filter((section) => !section.requiresPlatformAdmin || isPlatformAdmin)
		.map((section) => ({
			...section,
			items: section.items.filter(
				(item) =>
					(!item.requiresPlatformAdmin || isPlatformAdmin) &&
					(!item.requiredCapability ||
						hasSelectedCapability(item.requiredCapability)) &&
					(!item.requiredAnyCapabilities ||
						item.requiredAnyCapabilities.some(
							hasSelectedCapability,
						)) &&
					(!item.requiredBoundaryKind ||
						selectedTarget?.kind === item.requiredBoundaryKind) &&
					(!item.requiresBuilder || canAccessBuilder),
			),
		}))
		.filter((section) => section.items.length > 0); // Remove empty sections

	return (
		<>
			{/* Desktop Sidebar */}
			<aside
				className={cn(
					"hidden md:flex flex-col h-dvh border-r bg-background transition-all duration-300",
					isCollapsed ? "w-16" : "w-64",
				)}
			>
				{/* Logo Section */}
				<div
					className={cn(
						"h-16 flex items-center border-b",
						isCollapsed
							? "justify-center px-4"
							: "justify-start px-7",
					)}
				>
					{isCollapsed ? (
						<Logo type="square" className="h-10 w-10" alt="Logo" />
					) : (
						<Logo type="rectangle" className="h-8" alt="Logo" />
					)}
				</div>

				{/* Navigation */}
				<nav
					className={cn(
						"flex-1 flex flex-col gap-4 overflow-y-auto",
						isCollapsed ? "px-2 py-4" : "p-4",
					)}
				>
					{visibleSections.map((section) => (
						<div key={section.title} className="space-y-1">
							{!isCollapsed && (
								<h3 className="text-xs font-semibold text-muted-foreground mb-2 px-3 uppercase tracking-wider">
									{section.title}
								</h3>
							)}
							{section.items.map((item) => {
								const Icon = item.icon;
								const itemTitle = item.termKey
									? term(terminology, item.termKey, "plural")
									: item.title;
								return (
									<div key={item.href}>
										{item.dividerBefore && !isCollapsed && (
											<div className="my-2 mx-3 border-t border-border" />
										)}
										{item.dividerBefore && isCollapsed && (
											<div className="my-2 mx-2 border-t border-border" />
										)}
										<NavLink
											to={item.href}
											title={
												isCollapsed
													? itemTitle
													: undefined
											}
											className={({ isActive }) =>
												cn(
													"flex items-center rounded-lg text-sm font-medium transition-colors",
													"hover:bg-accent hover:text-accent-foreground",
													isActive
														? "bg-accent text-accent-foreground"
														: "text-muted-foreground",
													isCollapsed
														? "justify-center w-10 h-10 mx-auto"
														: "gap-3 px-3 py-2",
												)
											}
										>
											<Icon
												className={cn(
													isCollapsed
														? "h-5 w-5"
														: "h-4 w-4",
												)}
											/>
											{!isCollapsed && itemTitle}
										</NavLink>
									</div>
								);
							})}
						</div>
					))}
				</nav>
			</aside>

			{/* Mobile Sidebar Overlay */}
			{isMobileMenuOpen && (
				<div
					className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm md:hidden"
					onClick={() => setIsMobileMenuOpen(false)}
				>
					<aside
						className="fixed left-0 top-0 h-dvh w-64 border-r bg-background flex flex-col"
						onClick={(e) => e.stopPropagation()}
					>
						{/* Logo Section with Close Button */}
						<div className="h-16 flex items-center justify-between border-b px-4">
							<Logo type="rectangle" className="h-8" alt="Logo" />
							<Button
								variant="ghost"
								size="icon"
								onClick={() => setIsMobileMenuOpen(false)}
							>
								<X className="h-5 w-5" />
							</Button>
						</div>

						{/* Navigation */}
						<nav className="flex-1 flex flex-col gap-4 p-4 overflow-y-auto">
							{visibleSections.map((section) => (
								<div key={section.title} className="space-y-1">
									<h3 className="text-xs font-semibold text-muted-foreground mb-2 px-3 uppercase tracking-wider">
										{section.title}
									</h3>
									{section.items.map((item) => {
										const Icon = item.icon;
										const itemTitle = item.termKey
											? term(
													terminology,
													item.termKey,
													"plural",
												)
											: item.title;
										return (
											<div key={item.href}>
												{item.dividerBefore && (
													<div className="my-2 mx-3 border-t border-border" />
												)}
												<NavLink
													to={item.href}
													onClick={() =>
														setIsMobileMenuOpen(
															false,
														)
													}
													className={({ isActive }) =>
														cn(
															"flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
															"hover:bg-accent hover:text-accent-foreground",
															isActive
																? "bg-accent text-accent-foreground"
																: "text-muted-foreground",
														)
													}
												>
													<Icon className="h-4 w-4" />
													{itemTitle}
												</NavLink>
											</div>
										);
									})}
								</div>
							))}
						</nav>
					</aside>
				</div>
			)}
		</>
	);
}
